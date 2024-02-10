# nodejs_cross_compile

Tools for cross compile nodejs

## 背景
Nodejs的android跨平台编译实际上是无法在MacOS上使用的（也许只能在Linux系统上进行跨平台编译）。根据分析主要原因有两部分:
1. gyp生成的Makefile中，虽然cc等之类编译相关命令是已经根据toolset进行区分了。但是链接相关的命令却是根据target_os_来生成的，只有一套。但在NodeJS 编译过程中，需要先生成构建主机上能够运行的工具先。而在MacOS下，host对应的程序需要使用XCode进行编译，但Android程序则是用NDK。两者的链接相关命令是不兼容的。所以整个项目的编译必须分别使用host/guest两套程序对应的链接指令
2. nodejs的.gyp项目文件中，无论是否host/target, 其中的条件计算频繁使用的是"OS"这个变量(相当于只使用target_os)，而没有根据当前toolset以及对应的os来区分计算。

问题1还相对好解决，但问题2则需要大幅改动项目的gyp文件，而且gyp文件还很多。改起来太麻烦。所以就想了一个特殊的方法来绕过这些问题:  
    把编译过程根据toolset分成 host 和 target 两部分。先编译出host上跑的工具，然后再编译target部分。这样做来还带来了附带的好处，就是可以在构建多种CPU版本程序的时候，只需要执行一次host部分的build，对应cpu代码的build可以单独执行。
  
另外还有些小的问题:
  * 生成的Makefile里$AR的赋值有问题，根源是gyp源码里有bug
  * 由于缺失了一部分依赖关系，所以导致生成对应 --toolset=host的Makefile文件的默认构建行为少了一些target。所以需要用工具自动根据Makefile生成构建剩余target的构建脚本。


## 程序说明:
本程序主要有3大功能:
1. NodeJS源码下载  
使用 cross_compile.py download 可以下载最新版本，并解压到build/nodejs目录下，具体其他的参数使用 cross_compile.py download --help进行查看
2. 对源码进行Patch  
使用 cross_compile.py patch 自动执行patches目录下的所有补丁，默认node的源码目录为build/nodejs  
patch文件说明：
    * gyp_tools.patch  
      这个主要分成三个部分对gyp构建工具进行修改:
      * 修改对系统类型的判断，凡是判断"mac"的地方都增加"ios"的判断, 这部分修改基本上不影响原来的代码的兼容性。
      * 修改代码增加根据toolset来过滤生成的Makefile文件只包含指定的toolset. 仅对Makefile有效。
      * 修改了gyp/pylib/gyp/xcode_emulation.py中
        ```
            for libtoolflag in self._Settings().get ("OTHER_LDFLAGS", []):
        ```  
        为
        ```
            for libtoolflag in self._Settings().get("OTHER_LIBTOOLFLAGS", []):
        ```
        这个地方我觉得是gyp的bug，未能兼容最新的xcode project文件。
    * node_src_ext_v8.patch  
      这个修改是令node能使用最新版的v8引擎(不改代码无法编译通过). 同时应该也能兼容其自带的v8版本。
    * node_config.patch  
      修改node的配置和脚本来适应构建过程，这里的改动比较多。主要是增加了:
      * 支持android/ios编译
      * 支持使用外部的v8库（同时会使用v8自带的icu4c）
      * 使用系统自带的zlib (ios/android系统都自带zlib, 而且不用的话，android下会因缺少cpu_features.o而失败)
    * openssl_gyp.patch  
      修改openssl相关的gyp文件，使其能在android/ios下使用汇编版本进行编译
    * v8_gypfiles.patch  
    修改内置的v8 gyp配置，使其支持android/ios的编译

3. 构建
整个构建过程分成5步:
* host-configure  
生成用于构建host运行工具的Makefile
* host-build  
构建host运行的工具
* target-configure  
生成用于构建target程序的Makefile
* target-build  
构建target的程序
* install  
即make install

可以使用 --build-stage来指定执行那一步过程. 不指定的话，则执行所有过程. 例如
```
./cross_build.py build\
    --target-os=android\
    --target-arch=arm64\
    --node-root-dir=../node \
    --android-ndk-path=$ANDROID_NDK_ROOT\
    --node-config-arg="--partly-static"
    --build-stage="target-build"
```

## 执行步骤
### 1. 预备：
  * 本工具的下载准备
  ```
  # 下载本编译辅助工具。假设以下所有的程序都放在 ~/nodejs_build目录下
  mkdir ~/nodejs_build
  cd ~/nodejs_build
  git clone https://github.com/raymanzhang/nodejs_cross_compile.git

  # 安装python脚本的依赖库
  pip install requests tqdm
  ```  
  
  * 下载安装NDK(如果编译是ios版,可以跳过此步)
  必须使用ndk 26之前的版本，具体原因参见下面的备注说明
  ```
  # 下载android ndk并解压
  # linux平台下使用
  curl https://dl.google.com/android/repository/android-ndk-r25c-linux.zip -o ./android-ndk-r25c-linux.zip
  unzip ./android-ndk-r25c-linux.zip -d ~

  # mac平台则使用
  curl https://dl.google.com/android/repository/android-ndk-r25c-darwin.dmg -o ./android-ndk-r25c-darwin.dmg
  hdiutil attach ./android-ndk-r25c-darwin.dmg
  cp -r "/Volumes/Android NDK r25c/AndroidNDK9519653.app/Contents/NDK" ./android-ndk-r25c

  # 把环境变量ANDROID_NDK_ROOT指向该目录.
  export ANDROID_NDK_ROOT=~/node_build/android-ndk-r25c
  ```

  * 下载及编译v8. 如果不打算使用独立的v8可以不用执行下面这些步骤
  ```
  # 需要根据v8的文档用depot_tools下载相关的代码和工具
  # 下载过程可能需要使用代理，最好是用全局路由方式，否则很可能因为工具中的部分代码不支持代理设置而失败
  git clone https://chromium.googlesource.com/chromium/tools/depot_tools.git
  export PATH=~/nodejs_build/depot_tools:$PATH
  gclient
  mkdir v8
  cd v8
  fetch v8
  echo "target_os = ['android']" >> .gclient  # 如果是mac下可以改为: echo "target_os = ['android','ios']" >> .gclient
  cd v8
  git checkout 12.3.143
  gclient sync
  gn gen out/arm64.release
  cp ../../nodejs_cross_compile/v8_build/android_args.gn out/arm64.release/args.gn # 如果是编译ios版，则复制ios_args.gn
  ninja -C out/arm64.release v8_monolith
  ```

  注意：Mac下需要注意xcode command line tools的正确安装，有些情况下会导致xcrun 返回了命令工具的目录，而不是sdk的目录。遇到这个问题的时候可以使用xcode-select --reset重置一下即可。

### 2. 编译
  ```
  ./cross_build.py download  # 默认下载最新版并解压到 build/nodejs
  ./cross_build.py patch     # 对nodejs进行补丁

  # 对build/nodejs下的代码进行编译，默认生成"arm64"的cpu代码; 如果想生成的程序不需要依赖c++的标准动态库(libc++_shared.so)，则需要使用--partly-static

  Android版:
  # 直接使用nodejs自带的v8引擎进行编译执行: 
  ./cross_build.py build --target-os="android" --target-arch="arm64" --android-ndk-path=$ANDROID_NDK_ROOT --node-config-arg="--partly-static"

  # 使用外部的v8进行编译则执行:
  ./cross_build.py build --target-os="android" --target-arch="arm64" --android-ndk-path=$ANDROID_NDK_ROOT --node-config-arg="--partly-static" \
      --with-external-v8 \
      --v8-root-dir=../v8/v8 \
      --v8-obj-dir=../v8/v8/out/arm64.release/obj

  iOS版:
  # 直接使用nodejs自带的v8引擎进行编译执行: 
  ./cross_build.py build --target-os="ios" --target-arch="arm64" --node-config-arg="--partly-static"

  # 使用外部的v8进行编译则执行:
  ./cross_build.py build --target-os="ios" --target-arch="arm64" --node-config-arg="--partly-static" \
      --with-external-v8 \
      --v8-root-dir=../v8/v8 \
      --v8-obj-dir=../v8/v8/out/arm64.release/obj
  ```

### 3. 一些备注说明:
* iOS项目的默认目标平台为'13.4'
  觉得不合适的需要手工修改common.gypi及对应v8的args.gn里的值。IOS使用13.4的原因是ios下编译时有个参数(-fixup_chains)需要13.4以上的版本才支持。
* Android版本的编译如果是使用nodejs自带的v8，则必须使用ndk 26之前的版本. 
  原因是deps/v8/src/handles/handles.h 为了防止错误使用Handle类，做了一个类型检查. 而当不满足类型检查且使用clang，同时clang版本>=17的时候就static_assert(false). 这里利用了CWG2518了, CWG2518是当static_assert(false)出现在没有被实际用到的template代码里时不应该报错。理论上clang 17以上就支持. ndk26b里带的clang是17.0.2, 按理说是支持的。但实际上不行（可以写一段很简单的代码验证）。只能等ndk 27发布了再试试了。

* 程序和配置都是针对64位系统的，不支持32位。
  如果想要编译32位的，需要修改配置和程序，禁用pointer-compression和31bit_smis_on_64bit_arch。
* 独立v8的编译自带的icu4c默认不知道打包了什么数据，比nodejs自带的full-icu小很多。

## 支持情况汇总
当前程序仅对部分组合进行编译测试。android上编译后会进行通过adb进行简单运行试验，但ios版本则仅进行了编译。希望大家能反馈一下测试的结果和解决问题的思路。测试情况如下:
### 使用nodejs(v21.6.1)内置的v8
| OS | CPU | Configuration | Build | Run | Test |
| -- | -- | -- | -- | -- | -- |
| Android| X64 | Release | OK | OK | - |
| | arm64 | Release | OK | Failed | - |
| iOS | x64 (Simulator) | Release | - | - | - |
| | arm64 (Simulator) | Release | - | - | - |
| | arm64 (Device) | Release | OK | - | - |

### 使用独立的v8(12.3.143)
| OS | CPU | Configuration | Build | Run | Test |
| -- | -- | -- | -- | -- | -- |
| Android| X64 | Release | OK | OK | - |
| | arm64 | Release | OK | OK | - |
| iOS | x64 (Simulator) | Release | - | - | - |
| | arm64 (Simulator) | Release | - | - | - |
| | arm64 (Device) | Release | OK | - | - |

## TODO:
1. 支持动态库方式的构建
2. install的时候把.a也复制到安装目录下的lib中
3. 利用github Action来生成二进制版本供下载

