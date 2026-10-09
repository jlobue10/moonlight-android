# Host regression checks

Run each `test_*.py` with Python 3, JDK 17+ (`java` with the `jdk.compiler`
module), and g++ for the Vulkan test. The tests never connect to a controller,
start a stream, or load Android native libraries. They compile production
sources or extract production methods verbatim, with deterministic platform
boundaries. See each script for the exact boundary being replaced.

- `test_steam_ble.py`: actual BLE driver; queue limits, stale callbacks,
  disconnected rumble, subscription failures and write timeout recovery.
- `test_stereo_workers.py`: actual worker and delegate setup methods;
  buffer ownership, failure cleanup and interruption.
- `test_pyrowave_fences.py`: entire native renderer compiled against vendored
  Vulkan declarations; fake Vulkan dispatch simulates consecutive acquire
  timeouts. Also checks three container formats, truncations and 20,000
  deterministic malformed frames under ASan/UBSan.
- `test_pyrowave_state.py`: actual setup/HDR/cleanup methods; early HDR state.
- `test_depth_download.py`: actual downloader, fake HTTP streams and real files;
  cancellation overlapping a retry cannot delete the retry's private file.

The `--baseline` option on supported scripts uses the current HEAD's source,
which is useful before committing local fixes. To compare published revisions,
use the test scripts from this change against a checkout of the PR base.

For restricted containers where LeakSanitizer cannot inspect `/proc`, run the
native check with `ASAN_OPTIONS=detect_leaks=0`. Address and undefined-behavior
sanitizers stay enabled; that invocation does not test native leaks.

These checks do not replace Android Gradle/NDK/R8 builds or BLE, XR, Vulkan,
HDR display, tensor-quality and thermal/performance tests on real devices.
