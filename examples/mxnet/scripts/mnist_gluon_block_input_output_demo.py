import argparse
from mxnet import gluon, init, autograd
from mxnet.gluon import nn
from mxnet.gluon.data.vision import datasets, transforms
import time
import mxnet as mx
import tornasole.mxnet as tm
from tornasole.mxnet import TornasoleHook, SaveConfig, modes


def parse_args():
    parser = argparse.ArgumentParser(description='Train a mxnet gluon model for FashionMNIST dataset')
    parser.add_argument('--batch-size', type=int, default=256,
                        help='Batch size')
    parser.add_argument('--output-s3-uri', type=str, default='s3://tornasole-testing/block-io-mxnet-hook',
                        help='S3 URI of the bucket where tensor data will be stored.')
    opt = parser.parse_args()
    return opt

def acc(output, label):
    return (output.argmax(axis=1) ==
            label.astype('float32')).mean().asscalar()


def train_model(batch_size, net, train_data, valid_data, hook):
    softmax_cross_entropy = gluon.loss.SoftmaxCrossEntropyLoss()
    trainer = gluon.Trainer(net.collect_params(), 'sgd', {'learning_rate': 0.1})
    # Start the training.
    for epoch in range(1):
        train_loss, train_acc, valid_acc = 0., 0., 0.
        tic = time.time()
        hook.set_mode(modes.TRAIN)
        for data, label in train_data:
            data = data.as_in_context(mx.cpu(0))
            # forward + backward
            with autograd.record():
                output = net(data)
                loss = softmax_cross_entropy(output, label)
            loss.backward()
            # update parameters
            trainer.step(batch_size)
            # calculate training metrics
            train_loss += loss.mean().asscalar()
            train_acc += acc(output, label)
        # calculate validation accuracy
        hook.set_mode(modes.EVAL)
        for data, label in valid_data:
            data = data.as_in_context(mx.cpu(0))
            valid_acc += acc(net(data), label)
        print("Epoch %d: loss %.3f, train acc %.3f, test acc %.3f, in %.1f sec" % (
            epoch, train_loss / len(train_data), train_acc / len(train_data),
            valid_acc / len(valid_data), time.time() - tic))


def prepare_data(batch_size):
    mnist_train = datasets.FashionMNIST(train=True)
    X, y = mnist_train[0]
    ('X shape: ', X.shape, 'X dtype', X.dtype, 'y:', y)
    text_labels = ['t-shirt', 'trouser', 'pullover', 'dress', 'coat',
                   'sandal', 'shirt', 'sneaker', 'bag', 'ankle boot']
    X, y = mnist_train[0:10]
    transformer = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize(0.13, 0.31)])
    mnist_train = mnist_train.transform_first(transformer)
    train_data = gluon.data.DataLoader(
        mnist_train, batch_size=batch_size, shuffle=True, num_workers=4)
    mnist_valid = gluon.data.vision.FashionMNIST(train=False)
    valid_data = gluon.data.DataLoader(
        mnist_valid.transform_first(transformer),
        batch_size=batch_size, num_workers=4)
    return train_data, valid_data

# Create a model using gluon API. The tornasole hook is currently
# supports MXNet gluon models only.
def create_gluon_model():
    # Create Model in Gluon
    child_blocks = []
    net = nn.HybridSequential()
    conv2d_0 = nn.Conv2D(channels=6, kernel_size=5, activation='relu')
    child_blocks.append(conv2d_0)
    maxpool2d_0 = nn.MaxPool2D(pool_size=2, strides=2)
    child_blocks.append(maxpool2d_0)
    conv2d_1 = nn.Conv2D(channels=16, kernel_size=3, activation='relu')
    child_blocks.append(conv2d_1)
    maxpool2d_1 = nn.MaxPool2D(pool_size=2, strides=2)
    child_blocks.append(maxpool2d_1)
    flatten_0 = nn.Flatten()
    child_blocks.append(flatten_0)
    dense_0 = nn.Dense(120, activation="relu")
    child_blocks.append(dense_0)
    dense_1 = nn.Dense(84, activation="relu")
    child_blocks.append(dense_1)
    dense_2 = nn.Dense(10)
    child_blocks.append(dense_2)

    net.add(conv2d_0, maxpool2d_0, conv2d_1, maxpool2d_1, flatten_0, dense_0, dense_1, dense_2)
    net.initialize(init=init.Xavier(), ctx=mx.cpu())
    return net, child_blocks


# Create a tornasole hook. The initialization of hook determines which tensors
# are logged while training is in progress.
# Following function shows the hook initialization that enables logging of
# weights, biases and gradients in the model along with the inputs and output of the given
# child block.
def create_tornasole_hook(output_s3_uri, block):
    # Create a SaveConfig that determines tensors from which steps are to be stored.
    # With the following SaveConfig, we will save tensors for steps 1, 2 and 3.
    save_config = SaveConfig(save_steps=[1, 2, 3])

    # The names of input and output tensors of a block are in following format
    # Inputs :  <block_name>_input_<input_index>, and
    # Output :  <block_name>_output
    # In order to log the inputs and output of a model, we will create a collection as follows
    tm.get_collection(block.name).add_block_tensors(block, inputs=True, outputs=True)

    # Create a hook that logs weights, biases, gradients and inputs outputs of model while training.
    hook = TornasoleHook(out_dir=output_s3_uri, save_config=save_config, include_collections=[
        'weights', 'gradients', 'bias', block.name])
    return hook


def main():
    opt = parse_args()
    # Create a Gluon Model.
    net,child_blocks = create_gluon_model()

    # Create a tornasole hook for logging the desired tensors.
    # The output_s3_uri is a the URI for the s3 bucket where the tensors will be saved.
    output_s3_uri=opt.output_s3_uri

    # For creating a tornasole hook that can log inputs and output of the specific child block in the model,
    # we will pass the desired block object to the create_tornasole_hook function.
    # In the following case, we are attempting log inputs and output of the first Conv2D block.
    hook = create_tornasole_hook(output_s3_uri, child_blocks[0])

    # Register the hook to the top block.
    hook.register_hook(net)

    # Start the training.
    batch_size = opt.batch_size
    train_data, valid_data = prepare_data(batch_size)

    train_model(batch_size, net, train_data, valid_data, hook)

if __name__ == '__main__':
    main()