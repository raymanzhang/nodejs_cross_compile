# nodejs_cross_compile

Tools for cross-compiling Node.js.

## Background
Cross-compiling Node.js for Android is not supported on macOS (perhaps only possible on Linux systems). There are two main reasons for this:
1. The generated Makefile by gyp differentiates compilation-related commands like `cc` based on the toolset, but the linking commands are generated based on `target_os_`, resulting in a single set of commands. However, during the Node.js build process, tools that can run on the host need to be generated first. On macOS, the host programs need to be compiled using XCode, while Android programs use the NDK. The linking commands for these two platforms are incompatible. Therefore, the entire project compilation needs separate sets of linking instructions corresponding to the host and guest programs.
2. In the .gyp project files of Node.js, whether it is for the host or target, the conditions are frequently calculated using the "OS" variable (equivalent to using `target_os` only), without differentiating based on the current toolset and its corresponding OS.

Problem 1 is relatively easy to solve, but problem 2 requires major modifications to the project's gyp files, which are cumbersome as there are many of them. So, a special method was devised to bypass these issues:
1. The compilation process is divided into two parts based on the toolset: host and target. First, the tools running on the host are compiled, followed by the target part. This approach also brings additional benefits, such as only needing to build the host part once when building programs for multiple CPU versions, while the build for the corresponding CPU code can be executed separately.

There are also some minor issues:
- The assignment of `$AR` in the generated Makefile is incorrect, stemming from a bug in the gyp source code.
- Due to missing dependencies, the default build behavior of the Makefile for the corresponding `--toolset=host` file lacks some targets. Therefore, a tool is needed to automatically generate build scripts for the remaining targets based on the Makefile.

## Program Overview:
This program consists of three main functionalities:
1. Node.js source code download:  
   Using `cross_compile.py download`, you can download the latest version and extract it into the `build/nodejs` directory. For more specific parameters, use `cross_compile.py download --help` to view them.
2. Patching the source code:  
   Using `cross_compile.py patch` will automatically apply all patches in the `patches` directory to the Node.js source code. By default, the source code directory for Node.js is `build/nodejs`.
   Explanation of patch files:
   - `gyp_tools.patch`: This patch modifies the gyp build tool in three main aspects:
     - It adds the "ios" condition to all "mac" conditions, as the original code did not consider iOS. This modification does not impact the compatibility of the existing code.
     - It filters the generated Makefile to include only the specified toolset. This modification only affects the Makefile.
     - It fixes a bug in `gyp/pylib/gyp/xcode_emulation.py` where it changes:
       ```
       for libtoolflag in self._Settings().get ("OTHER_LDFLAGS", []):
       ```
       to:
       ```
       for libtoolflag in self._Settings().get("OTHER_LIBTOOLFLAGS", []):
       ```
       I believe this is a bug in gyp that fails to support the latest Xcode project files.
   - `node_src_ext_v8.patch`: This modification allows Node.js to use the latest version of the v8 engine and is necessary for successful compilation. It should also be compatible with the bundled v8 version.
   - `node_config.patch`: This patch makes various changes to Node.js configuration and scripts to adapt to the build process. These modifications are more extensive and primarily involve:
     - Adding support for Android/iOS compilation.
     - Enabling the use of an external v8 library (while using v8's own icu4c library).
     - Using the system's built-in zlib (both Android and iOS systems include zlib, and without it, the build process on Android may fail due to missing `cpu_features.o`).
   - `openssl_gyp.patch`: This patch modifies the gyp files related to OpenSSL, enabling compilation with assembly versions on Android/iOS.
   - `v8_gypfiles.patch`: This patch modifies the built-in v8 gyp configuration to enable Android/iOS compilation.

3. Build process:
   The entire build process consists of five steps:
   - host-configure: Generates the Makefile for building tools to run on the host.
   - host-build: Builds the tools for the host.
   - target-configure: Generates the Makefile for building the target program.
   - target-build: Builds the target program.
   - install: Equivalent to `make install`.

   You can use `--build-stage` to specify which step to execute. If not specified, all steps are executed. For example:
   ```
   ./cross_build.py build \
       --target-os=android \
       --target-arch=arm64 \
       --node-root-dir=../node \
       --android-ndk-path=$ANDROID_NDK_ROOT \
       --node-config-arg="--partly-static" \
       --build-stage="target-build"
   ```

## Execution Steps
### 1. Preparation:
- Download this tool:
  ```
  # Download this compilation auxiliary tool. Assuming all the programs are stored in the ~/nodejs_build directory.
  mkdir ~/nodejs_build
  cd ~/nodejs_build
  git clone https://github.com/raymanzhang/nodejs_cross_compile.git

  # Install the dependencies for the Python script
  pip install requests tqdm
  ```

- Download and install NDK (skip this step if compiling for iOS):
  You must use NDK version earlier than 26. Refer to the notes below for specific reasons.
  ```
  # Download and extract the Android NDK
  # For Linux:
  curl https://dl.google.com/android/repository/android-ndk-r25c-linux.zip -o ./android-ndk-r25c-linux.zip
  unzip ./android-ndk-r25c-linux.zip -d ~

  # For macOS:
  curl https://dl.google.com/android/repository/android-ndk-r25c-darwin.dmg -o ./android-ndk-r25c-darwin.dmg
  hdiutil attach ./android-ndk-r25c-darwin.dmg
  cp -r "/Volumes/Android NDK r25c/AndroidNDK9519653.app/Contents/NDK" ./android-ndk-r25c

  # Set the environment variable ANDROID_NDK_ROOT to point to the NDK directory.
  export ANDROID_NDK_ROOT=~/node_build/android-ndk-r25c
  ```

- Download and build v8 (Skip these steps if not using a separate v8):
  ```
  # Downloading related v8 code and tools using depot_tools, as per v8's documentation.
  # The download process may require a proxy, preferably using a globally routed method; otherwise, it may fail due to some proxy-related code in the tools.
  git clone https://chromium.googlesource.com/chromium/tools/depot_tools.git
  export PATH=~/nodejs_build/depot_tools:$PATH
  gclient
  mkdir v8
  cd v8
  fetch v8
  echo "target_os = ['android']" >> .gclient  # For macOS, change it to: echo "target_os = ['android','ios']" >> .gclient
  cd v8
  git checkout 12.3.143
  gclient sync
  gn gen out/arm64.release
  cp ../../nodejs_cross_compile/v8_build/android_args.gn out/arm64.release/args.gn  # For building iOS version, copy ios_args.gn instead
  ninja -C out/arm64.release v8_monolith
  ```

  Note: On macOS, ensure that Xcode command line tools are correctly installed. In some cases, `xcrun` may return the command tool's directory instead of the SDK directory. If you encounter this issue, simply reset it using `xcode-select --reset`.

### 2. Compilation:
```
./cross_build.py download  # Downloads the latest version and extracts it to build/nodejs by default
./cross_build.py patch     # Applies patches to Node.js

# Compile the code in the build/nodejs directory. By default, it generates "arm64" CPU code.
For Android:
# Directly using v8 engine bundled with Node.js for compilation and execution:
./cross_build.py build --target-os="android" --target-arch="arm64" --android-ndk-path=$ANDROID_NDK_ROOT --node-config-arg="--partly-static"

# Compiling using an external v8 engine:
./cross_build.py build --target-os="android" --target-arch="arm64" --android-ndk-path=$ANDROID_NDK_ROOT --node-config-arg="--partly-static" \
    --with-external-v8 \
    --v8-root-dir=../v8/v8 \
    --v8-obj-dir=../v8/v8/out/arm64.release/obj

For iOS:
# Directly using v8 engine bundled with Node.js for compilation and execution:
./cross_build.py build --target-os="ios" --target-arch="arm64" --node-config-arg="--partly-static"

# Compiling using an external v8 engine:
./cross_build.py build --target-os="ios" --target-arch="arm64" --node-config-arg="--partly-static" \
    --with-external-v8 \
    --v8-root-dir=../v8/v8 \
    --v8-obj-dir=../v8/v8/out/arm64.release/obj
```

### 3. Remarks:
- The default target platform for iOS projects is '13.4'.
   If you find it inappropriate, manual modifications are required in `common.gypi` and the corresponding `args.gn` within v8. The use of 13.4 for iOS is due to the `-fixup_chains` parameter during compilation, which requires iOS versions above 13.4.

- When using the bundled v8 in Android versions, it is necessary to use NDK versions earlier than 26.
   The reason is a type check in `deps/v8/src/handles/handles.h` to prevent incorrect usage of the `Handle` class, which performs a `static_assert(false)` when the type check fails and the `clang` version is >= 17. This takes advantage of CWG2518, which states that `static_assert(false)` should not produce an error when it appears in template code that is not actually used. In theory, `clang` 17 or higher should support this. The `clang` version included with NDK 26b is 17.0.2, which should support it. However, it doesn't work in practice (you can verify this with a simple code snippet). We'll have to wait until NDK 27 is released to try again.

- The program and configuration are specifically for 64-bit systems and do not support 32-bit. If you want to compile for 32-bit, you need to modify the configuration and program to disable pointer compression and use `31bit_smis_on_64bit_arch`.

- Compiling an independent v8 results in a significantly smaller bundled icu4c compared to the full-icu included with Node.js.

## Summary of Support
The current program has only been tested with certain combinations. After compiling on Android, a simple trial run is performed using `adb`, while for iOS, only compilation has been tested. It would be helpful to receive feedback on test results and approaches to resolving issues. The testing scenarios are as follows:

### Using the built-in v8 in Node.js (v21.6.1)
| OS      | CPU   | Configuration | Build | Run    | Test |
| ------- | ----- | ------------- | ----- | ------ | ---- |
| Android | X64   | Release       | OK    | OK     | -    |
|         | arm64 | Release       | OK    | Failed | -    |
| iOS     | x64 (Simulator) | Release       | - | - | - |
|         | arm64 (Simulator) | Release       | - | - | - |
|         | arm64 (Device) | Release       | OK | - | - |

### Using an independent v8 (12.3.143)
| OS      | CPU   | Configuration | Build | Run | Test |
| ------- | ----- | ------------- | ----- | --- | ---- |
| Android | X64   | Release       | OK    | OK  | -    |
|         | arm64 | Release       | OK    | OK  | -    |
| iOS     | x64 (Simulator) | Release       | - | - | - |
|         | arm64 (Simulator) | Release       | - | - | - |
|         | arm64 (Device) | Release       | OK    | -  | -    |

## TODO:
1. Support building dynamically linked libraries.
2. Copy the `.a` files to the installation directory's `lib` folder during `install`.
3. Use GitHub Actions to generate binary versions for download.