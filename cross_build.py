#!/usr/bin/env python3
import os
import platform
import sys
import argparse
import tarfile
import subprocess
import shutil
import re

import requests
from tqdm import tqdm
from urllib.parse import unquote

default_configure_opts = "--cross-compiling --without-npm --without-corepack --with-arm-float-abi=hard --with-arm-fpu=neon --experimental-enable-pointer-compression"

def get_filename_from_cd(cd):
    """
    Get filename from Content-Disposition header.
    """
    if not cd:
        return None
    fname = re.findall('filename=(.+)', cd)
    if len(fname) == 0:
        return None
    return unquote(fname[0])

def download_file(url, dest_folder=".", resume=True):
    """
    Downloads a file from the given URL and saves it to the specified destination folder.

    Args:
        url (str): The URL of the file to download.
        dest_folder (str, optional): The destination folder to save the downloaded file. Defaults to the current directory.
        resume (bool, optional): Whether to resume the download if the file already exists. Defaults to True.

    Returns:
        str: The path of the downloaded file if the download is successful, None otherwise.
    """
    try:
        response = requests.head(url)
        response.raise_for_status()
        total_size = int(response.headers.get('content-length', 0))
        
        # Get filename from Content-Disposition header
        filename = get_filename_from_cd(response.headers.get('content-disposition'))
        if not filename:
            filename = url.split("/")[-1]
        dest_path = os.path.join(dest_folder, filename)

        block_size = 1024  # 1 Kibibyte

        resume_header = {}
        if resume and os.path.exists(dest_path):
            local_size = os.path.getsize(dest_path)
            if local_size < total_size:
                resume_header['Range'] = f'bytes={local_size}-'
            else:
                print("File already fully downloaded. Skipping download.")
                return dest_path  # Already fully downloaded

        response = requests.get(url, headers=resume_header, stream=True)
        response.raise_for_status()

        with open(dest_path, 'ab') as file, tqdm(
            desc=filename,
            total=total_size,
            unit='B',
            unit_scale=True,
            unit_divisor=1024,
            initial=os.path.getsize(dest_path),
        ) as bar:
            for data in response.iter_content(block_size):
                bar.update(len(data))
                file.write(data)

        return dest_path  # Download successful
    except Exception as e:
        print(f"Error during download: {e}")
        return None  # Download failed
                            
def extract(file_path, extract_dir, remove_after_extract=False):
    """
    Extracts a tar.gz file to the specified directory.

    Args:
        file_path (str): The path to the tar.gz file.
        extract_dir (str): The directory where the file will be extracted.
        remove_after_extract (bool, optional): Whether to remove the file after extraction. Defaults to False.

    Returns:
        bool: True if the extraction is successful, False otherwise.
    """
    try:
        with tarfile.open(file_path, 'r:gz') as tar:
            tar.extractall(path=extract_dir)
        
        if remove_after_extract:
            os.remove(file_path)
        
        return True
    except tarfile.TarError as e:
        print(f"Error extracting file: {e}")
        return False
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return False
    
def get_latest_nodejs_version():
    """
    Retrieves the latest version of Node.js from the official website.

    Returns:
        str: The latest version of Node.js, or None if an error occurred.
    """
    url = 'https://nodejs.org/dist/latest'
    
    try:
        response = requests.get(url)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f'Error during HTTP request: {e}')
        return None
    
    try:
        pattern = re.compile(r'node-(v\d+\.\d+\.\d+)\.tar\.xz')
        match = pattern.search(response.text)
        
        if match:
            latest_version = match.group(1)
            return latest_version
        else:
            print('No version number found in the HTML.')
            return None
    except Exception as e:
        print(f'Error during version extraction: {e}')
        return None
    
def cmd_download(options):
    """
    Downloads and extracts the specified version of Node.js from the official Node.js distribution website.

    Args:
        options (object): An object containing the options for the download command.

    Returns:
        bool: True if the download and extraction process is successful, False otherwise.
    """
    if options.nodejs_version==None or options.nodejs_version=="":
        print("Getting latest nodejs version...")
        options.nodejs_version=get_latest_nodejs_version()
        print("Latest nodejs version:"+options.nodejs_version)
    os.umask(0o022)
    os.makedirs(options.dest_dir, exist_ok=True)
    
    nodejs_dist_url = 'https://nodejs.org/dist/'
    nodejs_url = nodejs_dist_url + options.nodejs_version + '/node-' + options.nodejs_version + '.tar.gz'

    downloaded_file=download_file(nodejs_url, options.dest_dir)
    if downloaded_file:
        print(f"Extracting source file:{downloaded_file}")
        if extract(downloaded_file, options.dest_dir, False):
            print("Done")
            try:
                os.rename( os.path.join( options.dest_dir, 'node-' + options.nodejs_version), os.path.join(options.dest_dir,"nodejs"))
                return True
            except Exception as e:
                print(f"Error renaming directory: {e}")
                return False
        else:
            print("Failed to extract source files")
            return False
    return False

def print_envs(env_vars):
    """
    Prints the environment variables.

    Args:
        env_vars (dict): A dictionary containing the environment variables.
    """
    for key in env_vars:
        if key in os.environ:
            value=os.environ[key]
            print(f"{key} = {value}")

def cmd_patch(options):
    """
    Apply a patch to the Node.js source code.

    Args:
        options (object): An object containing the options for the patching process.

    Returns:
        bool: True if the patch was successfully applied, False otherwise.
    """
    pathes_dir = os.path.join(os.path.dirname(os.path.abspath(os.path.expanduser(sys.argv[0]))),"patches")
    print("Patch files dir:" + pathes_dir)
    os.chdir(options.node_root_dir)
    
    patch_files = [f for f in os.listdir(pathes_dir) if f.endswith(".patch")]

    if not patch_files:
        print("No patch file found in '%s' !" % pathes_dir)
        return

    for patch_file in patch_files:
        patch_path = os.path.join(pathes_dir, patch_file)
        try:
            print(f"Applying patch:{patch_file}")
            subprocess.run(["patch", "-p1", "--input", patch_path], check=True)
            print(f"{patch_file} patched.")
        except subprocess.CalledProcessError as e:
            print(f"Fail to apply patch {patch_file}: {e}")
            return False
    
    return True

def run_make(parallel):
    """
    Run the 'make' command with the specified parallelism.

    Args:
        parallel (int): The number of parallel jobs to run.

    Returns:
        bool: True if the 'make' command succeeds, False otherwise.
    """
    print("Make parallel=" + str(parallel))
    return subprocess.run(f"make -j{int(parallel)} V=1", shell=True).returncode == 0

def run_configure(options, configure):
    """
    Runs the configure script for Node.js compilation.

    Args:
        options (object): An object containing configuration options.
        configure (str): The configure command to be executed.

    Returns:
        bool: True if the configure script was executed successfully, False otherwise.
    """
    os.chdir(options.node_root_dir)
    configure += default_configure_opts
    if options.with_external_v8:
        if options.v8_obj_dir and options.v8_root_dir:
            configure += f" --without-bundled-v8 --v8-obj-dir={options.v8_obj_dir} --v8-root-dir={options.v8_root_dir}"
        else:
            print("--with-external-v8 requires --v8-root-dir and --v8-obj-dir")
            return False
    if os.path.exists("./configure"):
        if options.prefix:
            configure += " --prefix="+options.prefix
        if options.node_config_args:
            configure += " " + options.node_config_args
        if options.verbose:
            print("Configure command:"+configure)
        result = subprocess.run(configure, shell=True)
        return result.returncode == 0
    else:
        print("Missing configure script")
        return False

def android_setenv(options):
    """
    Sets up the environment variables for building Android projects.

    Args:
        options (object): An object containing various build options.

    Returns:
        bool: True if the environment variables are set successfully.
    """
    if int(options.android_target_api_level) < 26:
        print("Android SDK version must be at least 26 (Android 8.0)")

    if options.target_arch == "arm":
        options.target_cpu = "arm"
        TOOLCHAIN_PREFIX = "armv7a-linux-androideabi"
    elif options.target_arch in ("aarch64", "arm64"):
        options.target_cpu = "arm64"
        TOOLCHAIN_PREFIX = "aarch64-linux-android"
        arch = "arm64"
    elif options.target_arch == "x86":
        options.target_cpu = "ia32"
        options.TOOLCHAIN_PREFIX = "i686-linux-android"
    elif options.target_arch in ("x86_64", "x64"):
        options.target_cpu = "x64"
        TOOLCHAIN_PREFIX = "x86_64-linux-android"
        options.target_arch = "x64"        

    if platform.system() == "Darwin":
        host_os = "darwin"
        toolchain_path = options.android_ndk_path + "/toolchains/llvm/prebuilt/darwin-x86_64"

    elif platform.system() == "Linux":
        host_os = "linux"
        toolchain_path = options.android_ndk_path + "/toolchains/llvm/prebuilt/linux-x86_64"

    CCACHE = "ccache " if options.use_ccache else ""
    os.environ['PATH'] += os.pathsep + toolchain_path + "/bin"
    os.environ['CC'] = f"{CCACHE}{toolchain_path}/bin/{TOOLCHAIN_PREFIX}{options.android_target_api_level}-clang"
    os.environ['CXX'] = f"{CCACHE}{toolchain_path}/bin/{TOOLCHAIN_PREFIX}{options.android_target_api_level}-clang++"
    os.environ['AR'] = f"{toolchain_path}/bin/llvm-ar"
    
    GYP_DEFINES = "target_arch=" + options.target_arch
    GYP_DEFINES += " v8_target_arch=" + options.target_arch
    GYP_DEFINES += " android_target_arch=" + options.target_arch
    GYP_DEFINES += " host_os=" + host_os + " OS=android"
    GYP_DEFINES += " android_ndk_path=" + options.android_ndk_path
    os.environ['GYP_DEFINES'] = GYP_DEFINES
    
    if options.verbose:
        print("Android build environment:")
        print_envs(['PATH','CC', 'CXX', 'AR', 'CPPFLAGS', 'CXXFLAGS', 'LDFLAGS', 'GYP_DEFINES'])
    return True

def android_configure(options):
    """
    Configures the Android build environment and runs the configure script.

    Args:
        options (dict): A dictionary containing the build options.

    Returns:
        str: The output of the configure script.

    """
    os.chdir(options.node_root_dir)
    android_setenv(options)
    configure=f"./configure --dest-cpu={options.target_cpu} --dest-os=android --toolset=target "
    return run_configure(options, configure)
            
def android_build(options):
    os.chdir(options.node_root_dir)
    android_setenv(options)
    run_make(options.parallel)

def ios_setenv(options):
    """
    Sets the environment variables for iOS build.

    Args:
        options (object): An object containing build options.

    Returns:
        bool: True if the environment variables are set successfully.
    """
    CCACHE = "ccache " if options.use_ccache else ""
    os.environ['CC'] = CCACHE + "clang"
    os.environ['CXX'] = CCACHE + "clang++"
    options.target_os="ios"
                
    gyp_defines = f"target_arch={options.target_arch} v8_target_arch={options.target_arch} host_os=darwin OS={options.target_os}  ios_sdk={options.apple_platform.lower()}"
    os.environ['GYP_DEFINES'] = gyp_defines
    if options.verbose:
        print("iOS build environment:")
        print_envs(['DEVELOPER_DIR', 'IPHONEOS_DEPLOYMENT_TARGET', 'MACOSX_DEPLOYMENT_TARGET', 'CC', 'CXX', 'CPPFLAGS', 'CXXFLAGS', 'LDFLAGS', 'GYP_DEFINES'])
    return True

def ios_configure(options):
    """
    Configures the iOS build environment and runs the configure command.

    Args:
        options (dict): A dictionary containing the build options.

    Returns:
        str: The output of the configure command.
    """
    os.chdir(options.node_root_dir)
    ios_setenv(options)
    configure=f"./configure --dest-os={options.target_os} --dest-cpu={options.target_arch} --toolset=target "
    return run_configure(options, configure)

def ios_build(options):
    os.chdir(options.node_root_dir)  
    ios_setenv(options)
    return run_make(options.parallel)

def host_configure(options):
    """
    Configure the host environment for cross-compiling.

    Args:
        options (dict): A dictionary containing configuration options.

    Returns:
        str: The result of running the configure command.
    """
    os.chdir(options.node_root_dir)
    if options.use_ccache:
        CC = os.environ.get('CC', 'cc' if sys.platform == 'darwin' else 'gcc')
        CXX = os.environ.get('CXX', 'c++' if sys.platform == 'darwin' else 'g++')
        os.environ['CC'] = 'ccache ' + CC
        os.environ['CXX'] = 'ccache ' + CXX
    configure="./configure --toolset=host "
    return run_configure(options, configure)
    
import subprocess

def generate_host_target_build_script(options):
    """
    Generates a build script for the host target based on the generated Makefile.

    Args:
        options (object): An object containing the necessary options for generating the build script.

    Returns:
        str or None: The generated build script if successful, None otherwise.
    """
    os.chdir(options.node_root_dir)
    shell_cmd="""grep include out/Makefile|grep host.mk|awk '{print $2}'|sed 's/\.host\.mk//'|sed 's/.*\///'|awk '{print "make -j%d "$1}' """ % int(options.parallel)
    result = subprocess.run(shell_cmd, shell=True, capture_output=True, text=True)
    if result.returncode==0:
        return result.stdout
    else:
        return None

def host_build(options):
    """
    Build the host target using the specified options.

    Args:
        options (dict): The options for building the host target.

    Returns:
        bool: True if the build is successful, False otherwise.
    """
    os.chdir(options.node_root_dir)
    if run_make(options.parallel): # You need to 'make' once, then forcibly 'make' the remaining targets
        build_script=generate_host_target_build_script(options)
        if build_script:
            os.chdir(os.path.join(options.node_root_dir,"out"))
            return subprocess.run(build_script, shell=True).returncode==0
        else:
            return False    
    return False

def cmd_build(options):
    """
    Build the Node.js source code based on the provided options.

    Args:
        options (object): An object containing the build options.

    Returns:
        bool: True if the build is successful, False otherwise.
    """
    result = True
    if options.verbose:
        print("Node source directory:" + options.node_root_dir)
        
    # Set the prefix if not provided
    if options.prefix == None or options.prefix == "":
        options.prefix = f"install/{options.target_arch}-{options.target_os}"
        if options.target_os == "ios":
            options.prefix += "-" + options.apple_platform.lower()
    if options.prefix:
        options.prefix = os.path.abspath(options.prefix)

    if options.with_external_v8:
        if options.v8_obj_dir:
            options.v8_obj_dir = os.path.abspath(os.path.expanduser(options.v8_obj_dir))
        else:
            print("Missing v8 object directory")
            return False
        if options.v8_root_dir:
            options.v8_root_dir = os.path.abspath(os.path.expanduser(options.v8_root_dir))
        else:
            print("Missing v8 source directory")
            return False

    # Perform host configuration
    if options.build_stage == None or options.build_stage == "host-configure":
        result = host_configure(options)

    # Perform host build
    if result and (options.build_stage == None or options.build_stage == "host-build"):
        result = host_build(options)

    # Perform target configuration based on the target OS
    if result and (options.build_stage == None or options.build_stage == "target-configure"):
        if options.target_os == "android":
            if options.android_ndk_path:
                result = android_configure(options)
            else:
                print("Missing parameter: --android-ndk-path")
                return False
        elif options.target_os == "ios":
            result = ios_configure(options)

    # Perform target build based on the target OS
    if result and (options.build_stage == None or options.build_stage == "target-build"):
        if options.target_os == "android":
            result = android_build(options)
        elif options.target_os == "ios":
            result = ios_build(options)

    # Install the build artifacts
    if result and (options.build_stage == None or options.build_stage == "install"):
        print("Install to:" + options.prefix)
        os.chdir(options.node_root_dir)
        if options.target_os == "android":
            android_setenv(options)
        elif options.target_os == "ios":
            ios_setenv(options)
        result = subprocess.run(f"make install", shell=True).returncode == 0

    return result

class ExplicitDefaultsHelpFormatter(argparse.ArgumentDefaultsHelpFormatter):
    def _get_help_string(self, action):
        if action.default is None or action.default is False:
            return action.help
        return super()._get_help_string(action)
    
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nodejs cross-compiler build script.", formatter_class=ExplicitDefaultsHelpFormatter)
    subparsers = parser.add_subparsers(title='Commands', dest='command', help='Commands')

    download_parser = subparsers.add_parser('download', help='Download nodejs source code archive and extract', formatter_class=ExplicitDefaultsHelpFormatter)
    download_parser.add_argument('--node-version', dest='nodejs_version', required=False, help='Node version to be downloaded, default to latest version')
    download_parser.add_argument('--dest-dir', dest='dest_dir', default='build', required=False, help='Destination directory')
    download_parser.set_defaults(func=cmd_download)

    patch_parser = subparsers.add_parser('patch', help='Patch gyp tools in nodejs', formatter_class=ExplicitDefaultsHelpFormatter)
    patch_parser.add_argument("--node-root-dir", dest="node_root_dir", default="build/nodejs", help="Nodejs source root directory")
    patch_parser.set_defaults(func=cmd_patch)

    build_parser = subparsers.add_parser('build', help='Build nodejs', formatter_class=ExplicitDefaultsHelpFormatter)
    build_parser.add_argument('--target-os', choices=["android", "ios"], required=True, help='Target OS for build')
    build_parser.add_argument('--target-arch', default="arm64", dest="target_arch", choices=["x86_64", "arm64"], help="Target architecture.")
    build_parser.add_argument("--node-root-dir", dest="node_root_dir", default="build/nodejs", help="Nodejs source root directory")
    build_parser.add_argument('--build-stage', dest='build_stage', required=False, choices=["host-configure", "host-build", "target-configure", "target-build", "install"], help="Only do the specified build stage")
    build_parser.add_argument('--parallel', default=max(1, round(os.cpu_count()/4*3)), help='Parallel job numbers of make command, default is 1/2 of CPU cores including hyperthreading')
    build_parser.add_argument('--use-ccache', default=True, action='store_true', help='Whether to use ccache')
    build_parser.add_argument('--prefix', help='Prefix of install directory')

    build_parser.add_argument('--with-external-v8', default=False, action='store_true', help='Use external v8')
    build_parser.add_argument('--v8-obj-dir', help='V8 prebuilt object directory, for example ~/source/v8/v8/out/arm64.release/obj')
    build_parser.add_argument('--v8-root-dir', help='V8 source root directory, for example ~/source/v8/v8')
    
    build_parser.add_argument('--node-config-args', help='Arguments for node configure script')
    build_parser.add_argument('--verbose', default=True, action='store_true', help='Verbose output')
    build_parser.set_defaults(func=cmd_build)

    build_parser.add_argument('--android-ndk-path', dest="android_ndk_path", required=False, help='Android NDK path')
    build_parser.add_argument('--android-target-api-level', default=24, required=False, help='Android Target API Level(>=24)')
    build_parser.add_argument('--apple-platform', default="iPhoneOS", choices=["iPhoneOS","iPhoneSimulator","AppleTVOS","AppleTVSimulator", "MacCatalyst"], required=False, help='iOS platform')

    args = parser.parse_args()
    if args.command ==None:
        parser.print_help()
    else:
        if args.command=="build" and args.use_ccache and shutil.which("ccache") is None:
            print("ccache not found, disable ccache")
            args.use_ccache = False
        if "node_root_dir" in args and args.node_root_dir:
            args.node_root_dir = os.path.abspath(os.path.expanduser(args.node_root_dir))
        args.func(args)
