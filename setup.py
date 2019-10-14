import os
import sys
import setuptools

exec(open("tornasole/_version.py").read())
CURRENT_VERSION = __version__
FRAMEWORKS = ['tensorflow', 'pytorch', 'mxnet', 'xgboost']


def compile_summary_protobuf():
    proto_paths = ['tornasole/core/tfevent/proto', 'tornasole/pytorch/proto']
    cmd = 'set -ex && protoc '
    for proto_path in proto_paths:
        proto_files = os.path.join(proto_path, '*.proto')
        cmd += proto_files + ' '
        print('compiling protobuf files in {}'.format(proto_path))
    cmd += ' --python_out=.'
    return os.system(cmd)


def get_framework_packages(f):
    return ['tornasole.' + f + '*', 'tests.' + f + '*']


def get_frameworks_to_build():
    only_rules = os.environ.get('TORNASOLE_FOR_RULES', False)
    if only_rules in ['1', 'True', 'true']:
        only_rules = True
    else:
        only_rules = False

    with_frameworks = {}
    if not only_rules:
        for f in FRAMEWORKS:
            with_frameworks[f] = os.environ.get('TORNASOLE_WITH_' + f.upper(), False)
            if with_frameworks[f] in ['1', 'True', 'true']:
                with_frameworks[f] = True
            else:
                with_frameworks[f] = False
        enabled_some_framework = any(with_frameworks.values())

        if not enabled_some_framework:
            print('Building for all frameworks in one package')
            for f in FRAMEWORKS:
                with_frameworks[f] = True
    else:
        for f in FRAMEWORKS:
            with_frameworks[f] = False
    return with_frameworks


def get_packages_to_include(frameworks_to_build):
    exclude_packages = []
    include_framework_packages = []
    for f in FRAMEWORKS:
        fp = get_framework_packages(f)
        exclude_packages.extend(fp)
        if frameworks_to_build[f]:
            include_framework_packages.extend(fp)
    include = setuptools.find_packages(exclude=exclude_packages)
    include.extend(include_framework_packages)
    packages = setuptools.find_packages(include=include)
    print(packages)
    return packages


def get_tests_packages(frameworks_to_build):
    tests_packages = ['pytest']
    for f, v in frameworks_to_build.items():
        if v:
            if f in ['tensorflow', 'mxnet', 'xgboost']:
                tests_packages.append(f)
            if f == 'pytorch':
                tests_packages.extend(['torch', 'torchvision'])
    return tests_packages


def build_package(version):
    # todo: fix long description
    # with open('docs/'+ name + '/README.md', "r") as fh:
    #     long_description = fh.read()

    frameworks_to_build = get_frameworks_to_build()
    tests_packages = get_tests_packages(frameworks_to_build)
    packages = get_packages_to_include(frameworks_to_build)
    setuptools.setup(
        name='tornasole',
        version=version,
        author="The Tornasole Team",
        author_email="tornasole@amazon.com",
        description="Tornasole",
        # long_description=long_description,
        # long_description_content_type="text/markdown",
        url="https://github.com/awslabs/tornasole_core",
        packages=packages,
        classifiers=[
            "Programming Language :: Python :: 3",
            "License :: OSI Approved :: Apache Software License",
            "Operating System :: OS Independent",
        ],
        #pinning aioboto3 version as aiobot3 is pinning versions
        # https://github.com/aio-libs/aiobotocore/issues/718
        install_requires = ['aioboto3==6.4.1', 'nest_asyncio',
                            'protobuf>=3.6.0' ,'botocore==1.12.91',
                            'boto3==1.9.91', 'aiobotocore==0.10.2',
                            'numpy', 'joblib'],
        setup_requires=["pytest-runner"],
        tests_require=tests_packages,
        python_requires='>=3.6'
    )


if compile_summary_protobuf() != 0:
    print('ERROR: Compiling summary protocol buffers failed. You will not be '
          'able to use Tornasole.'
          'Please make sure that you have installed protobuf3 '
          'compiler and runtime correctly.')
    sys.exit(1)

build_package(version=CURRENT_VERSION)
