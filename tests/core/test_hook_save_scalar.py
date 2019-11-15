# Standard Library
import json
import os
import shutil
from datetime import datetime

# Third Party
import mxnet as mx
import pytest
import tensorflow as tf
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from mxnet import autograd, gluon, init
from mxnet.gluon import nn as mxnn
from tensorflow import keras
from torch.autograd import Variable

# First Party
import smdebug.mxnet as tm
import smdebug.pytorch as tp
import smdebug.tensorflow as tt
from smdebug.core.config_constants import DEFAULT_SAGEMAKER_METRICS_PATH
from smdebug.core.modes import ModeKeys
from smdebug.core.save_config import SaveConfig, SaveConfigMode
from smdebug.mxnet import Hook as MX_Hook
from smdebug.pytorch import Hook as PT_Hook
from smdebug.tensorflow import KerasHook as TF_Hook
from smdebug.trials import create_trial

SMDEBUG_PT_HOOK_TESTS_DIR = "test_output/smdebug_pt/tests/"
SMDEBUG_MX_HOOK_TESTS_DIR = "test_output/smdebug_mx/tests/"
SMDEBUG_TF_HOOK_TESTS_DIR = "test_output/smdebug_tf/tests/"


def simple_pt_model(hook, steps=10, register_loss=False):
    """
    Create a PT model. save_scalar() calls are inserted before, during and after training.
    Only the scalars with searchable=True will be written to a metrics file.
    """

    class Net(nn.Module):
        def __init__(self):
            super(Net, self).__init__()
            self.add_module("conv1", nn.Conv2d(1, 20, 5, 1))
            self.add_module("relu0", nn.ReLU())
            self.add_module("max_pool", nn.MaxPool2d(2, stride=2))
            self.add_module("conv2", nn.Conv2d(20, 50, 5, 1))
            self.add_module("relu1", nn.ReLU())
            self.add_module("max_pool2", nn.MaxPool2d(2, stride=2))
            self.add_module("fc1", nn.Linear(4 * 4 * 50, 500))
            self.add_module("relu2", nn.ReLU())
            self.add_module("fc2", nn.Linear(500, 10))

        def forward(self, x):
            x = self.relu0(self.conv1(x))
            x = self.max_pool(x)
            x = self.relu1(self.conv2(x))
            x = self.max_pool2(x)
            x = x.view(-1, 4 * 4 * 50)
            x = self.relu2(self.fc1(x))
            x = self.fc2(x)
            return F.log_softmax(x, dim=1)

    model = Net().to(torch.device("cpu"))
    criterion = nn.NLLLoss()
    hook.register_hook(model)
    if register_loss:
        hook.register_loss(criterion)
    model.train()
    optimizer = optim.SGD(model.parameters(), lr=0.001, momentum=0.9)

    hook.save_scalar("pt_before_train", 1, searchable=False)
    hook.set_mode(ModeKeys.TRAIN)
    for i in range(steps):
        batch_size = 32
        data, target = torch.rand(batch_size, 1, 28, 28), torch.rand(batch_size).long()
        data, target = data.to(torch.device("cpu")), target.to(torch.device("cpu"))
        optimizer.zero_grad()
        output = model(Variable(data, requires_grad=True))
        if register_loss:
            loss = criterion(output, target)
        else:
            loss = F.nll_loss(output, target)
        hook.save_scalar("pt_train_loss", loss.item(), searchable=True)
        loss.backward()
        optimizer.step()
    hook.save_scalar("pt_after_train", 1, searchable=False)

    return ["scalar/pt_before_train", "scalar/pt_train_loss", "scalar/pt_after_train"]


def simple_mx_model(hook, steps=10, register_loss=False):
    """
    Create a MX model. save_scalar() calls are inserted before, during and after training.
    Only the scalars with searchable=True will be written to a metrics file.
    """
    net = mxnn.HybridSequential()
    net.add(
        mxnn.Conv2D(channels=6, kernel_size=5, activation="relu"),
        mxnn.MaxPool2D(pool_size=2, strides=2),
        mxnn.Conv2D(channels=16, kernel_size=3, activation="relu"),
        mxnn.MaxPool2D(pool_size=2, strides=2),
        mxnn.Flatten(),
        mxnn.Dense(120, activation="relu"),
        mxnn.Dense(84, activation="relu"),
        mxnn.Dense(10),
    )
    net.initialize(init=init.Xavier(), ctx=mx.cpu())
    hook.register_hook(net)

    train_loss = 0.0
    softmax_cross_entropy = gluon.loss.SoftmaxCrossEntropyLoss()
    if register_loss:
        hook.register_hook(softmax_cross_entropy)
    trainer = gluon.Trainer(net.collect_params(), "sgd", {"learning_rate": 0.1})

    hook.save_scalar("mx_before_train", 1, searchable=False)
    hook.set_mode(ModeKeys.TRAIN)
    for i in range(steps):
        batch_size = 32
        data, target = mx.random.randn(batch_size, 1, 28, 28), mx.random.randn(batch_size)
        data = data.as_in_context(mx.cpu(0))
        with autograd.record():
            output = net(data)
            loss = softmax_cross_entropy(output, target)
        loss.backward()
        # update parameters
        trainer.step(batch_size)
        # calculate training metrics
        train_loss += loss.mean().asscalar()
        hook.save_scalar("mx_train_loss", loss.mean().asscalar(), searchable=True)
    hook.save_scalar("mx_after_train", 1, searchable=False)

    return ["scalar/mx_before_train", "scalar/mx_train_loss", "scalar/mx_after_train"]


def simple_tf_model(hook, steps=10, lr=0.4):
    """
    Create a TF model. Tensors registered with the SEARCHABLE_SCALARS collection will be logged
    to the metrics file.
    """
    mnist = keras.datasets.mnist

    (x_train, y_train), (x_test, y_test) = mnist.load_data()
    x_train, x_test = x_train / 255.0, x_test / 255.0

    relu_layer = keras.layers.Dense(128, activation="relu")

    model = keras.models.Sequential(
        [
            keras.layers.Flatten(input_shape=(28, 28)),
            relu_layer,
            keras.layers.Dropout(0.2),
            keras.layers.Dense(10, activation="softmax"),
        ]
    )

    opt = tf.train.RMSPropOptimizer(lr)
    opt = hook.wrap_optimizer(opt)

    model.compile(
        optimizer=opt,
        loss="sparse_categorical_crossentropy",
        run_eagerly=False,
        metrics=["accuracy"],
    )
    hooks = [hook]

    hook.set_mode(ModeKeys.TRAIN)
    model.fit(x_train, y_train, epochs=steps, steps_per_epoch=steps, callbacks=hooks, verbose=0)


def delete_local_trials(local_trials):
    for trial in local_trials:
        shutil.rmtree(trial)


def check_trials(out_dir, save_steps, coll_name, saved_scalars=None):
    """
    Create trial to check if non-scalar data is written as per save config and
    check whether all the scalars written through save_scalar have been saved.
    """
    trial = create_trial(path=out_dir, name="test output")
    assert trial
    tensor_list = set(trial.tensors()) & set(trial.tensors(collection=coll_name))
    for tname in tensor_list:
        if tname not in saved_scalars:
            assert len(trial.tensor(tname).steps()) == len(save_steps)
    scalar_list = trial.tensors(regex="^scalar")
    if scalar_list:
        assert len(set(saved_scalars) & set(scalar_list)) == len(saved_scalars)


def check_metrics_file(saved_scalars):
    """
    Check the SageMaker metrics file to ensure that all the scalars saved using
    save_scalar(searchable=True) or mentioned through SEARCHABLE_SCALARS collections, have been saved.
    """
    METRICS_DIR = os.environ.get(DEFAULT_SAGEMAKER_METRICS_PATH, ".")
    file_name = "{}/{}.json".format(METRICS_DIR, str(os.getpid()))
    scalarnames = set()
    with open(file_name) as fp:
        for line in fp:
            data = json.loads(line)
            scalarnames.add(data["MetricName"])
    assert scalarnames
    assert len(set(saved_scalars) & set(scalarnames)) > 0


def pt_save_scalar(run_id, save_config, coll, save_steps, register_loss=False):
    """
    Test save_scalar() with a PyTorch model
    """
    trial_dir = os.path.join(SMDEBUG_PT_HOOK_TESTS_DIR, run_id)
    coll_name, coll_regex = coll
    collection = tp.get_collection(coll_name)
    collection.include([coll_regex])
    collection.save_config = save_config
    hook = PT_Hook(out_dir=trial_dir, include_collections=[coll_name], export_tensorboard=True)
    saved_scalars = simple_pt_model(hook, register_loss=register_loss)
    hook._cleanup()
    check_trials(trial_dir, save_steps, coll_name, saved_scalars)
    check_metrics_file(saved_scalars)


def mx_save_scalar(run_id, save_config, coll, save_steps, register_loss=False):
    """
        Test save_scalar() with an MXNet model
    """
    trial_dir = os.path.join(SMDEBUG_MX_HOOK_TESTS_DIR, run_id)
    coll_name, coll_regex = coll
    collection = tm.get_collection(coll_name)
    collection.include([coll_regex])
    collection.save_config = save_config
    hook = MX_Hook(out_dir=trial_dir, include_collections=[coll_name], export_tensorboard=True)
    saved_scalars = simple_mx_model(hook, register_loss=register_loss)
    hook._cleanup()
    check_trials(trial_dir, save_steps, coll_name, saved_scalars)
    check_metrics_file(saved_scalars)


def tf_save_scalar(run_id, save_config, coll, save_steps):
    """
        Test searchable_scalars collection with a Tensorflow model
    """
    trial_dir = os.path.join(SMDEBUG_TF_HOOK_TESTS_DIR, run_id)
    hook = TF_Hook(
        out_dir=trial_dir,
        save_config=save_config,
        include_collections=[coll.name],
        export_tensorboard=True,
    )
    simple_tf_model(hook)
    hook._cleanup()
    check_trials(trial_dir, save_steps, "searchable_scalars", ["loss"])
    check_metrics_file(["loss"])


@pytest.mark.slow  # 1:02 to run
def test_save_scalar():
    saveconfigs = [
        SaveConfig(save_steps=[0, 2, 4, 6, 8]),
        SaveConfig(
            {
                ModeKeys.TRAIN: SaveConfigMode(save_interval=2),
                ModeKeys.GLOBAL: SaveConfigMode(save_interval=3),
            }
        ),
    ]
    collections = [("all", ".*"), ("scalars", "^scalar")]
    register_loss = [True, False]

    # Test save_scalar() with PyTorch and MXNet models with different options
    # for the collections saved, save configs, and whether loss is registered or not.
    for save_config in saveconfigs:
        save_steps = save_config.get_save_config(ModeKeys.TRAIN).save_steps
        if not save_steps:
            save_interval = save_config.get_save_config(ModeKeys.TRAIN).save_interval
            save_steps = [i for i in range(0, 10, save_interval)]
        for coll in collections:
            for reg_loss in register_loss:
                run_id = "trial_" + coll[0] + "-" + datetime.now().strftime("%Y%m%d-%H%M%S%f")
                pt_save_scalar(run_id, save_config, coll, save_steps, register_loss=reg_loss)
                mx_save_scalar(run_id, save_config, coll, save_steps, register_loss=reg_loss)

    # Tensorflow doesn't use save_scalar(). It uses a collection "SEARCHABLE_SCALARS" instead.
    # Test this collection with a Tensorflow model
    coll = tt.get_collection("searchable_scalars")
    coll.include("loss")
    run_id = "trial_" + coll.name + datetime.now().strftime("%Y%m%d-%H%M%S%f")
    tf_save_scalar(
        run_id, saveconfigs[0], coll, saveconfigs[0].get_save_config(ModeKeys.TRAIN).save_steps
    )

    # Delete the trial directories
    delete_local_trials(
        [SMDEBUG_PT_HOOK_TESTS_DIR, SMDEBUG_MX_HOOK_TESTS_DIR, SMDEBUG_TF_HOOK_TESTS_DIR]
    )