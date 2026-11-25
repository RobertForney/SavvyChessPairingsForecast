# Browser, Apple, and Android support

Savvy Pairings uses one responsive web interface that can also be installed as a Progressive Web App (PWA). The installed app opens in its own window and uses the same pairing service as the browser. The repository contains the manifest, launch icons, service worker, install instructions, and mobile layout; it does not contain native iOS, macOS, or Android binaries.

| Platform | Run in browser | Install option |
| --- | --- | --- |
| iPhone / iPad | Safari | Share → Add to Home Screen; enable **Open as Web App** if shown |
| Mac | Safari, Chrome, Edge, or Firefox | Safari on macOS Sonoma or later: File → Add to Dock; Chrome / Edge: browser install command when offered |
| Android | Chrome or another modern browser | Chrome menu → Add to Home screen / Install app; wording and installation type depend on the browser |
| Windows / Linux | A modern browser | Chrome / Edge browser install command when offered |

The **Install app** button uses the browser's installation prompt when available and otherwise shows instructions. The button does not imply that every browser supports installation. Safari uses its own installation menus; `beforeinstallprompt` is not required. See [Apple's Mac web app instructions](https://support.apple.com/en-ie/104996), [Apple's iPhone instructions](https://support.apple.com/guide/iphone/bookmark-a-website-iph42ab2f3a7/ios), and [Chrome's cross-browser installation overview](https://web.dev/learn/pwa/installation).

## Pairing service and offline behavior

Fresh forecasts, edits, imports, exports from the service, and tournament simulation require a reachable backend. The Python engines and the BBP executable run on that backend, not inside the phone's browser. An installed PWA therefore does not provide an offline pairing engine.

The service worker caches only an explicit list of public app files: the HTML shell, JavaScript, stylesheet, manifest, offline help page, and icons. `/api/` requests always use the network, including GET health, state, sample, simulation, and forecast requests. Failed API connections return a **503 JSON response**, never cached forecasts or an HTML success page. Mutation requests are not queued for later execution. The source XLSX is not downloaded or cached by installing the app.

The main app may save the most recently loaded workspace in this browser's local storage. When that saved view is shown without the backend, it is read only and visibly marked as saved. It cannot establish that a forecast is current. A first visit without a successful connection has no tournament snapshot to restore. Browser storage can be cleared or evicted; use explicit tournament exports for backups. Private browser windows and installed apps can have different storage contexts.

The connection banner distinguishes browser-reported offline status from an unavailable pairing service. Reconnecting checks `/api/health`; the **Reconnect** action refreshes the live workspace after the service responds. Automatic connectivity changes do not reload a form with unsaved edits.

## Run locally and test in the browser

From the repository root:

```powershell
python -m app.server
```

Open `http://127.0.0.1:8000` on that computer. Localhost is a secure-context exception for service worker development. Chrome or Edge developer tools can inspect **Application → Manifest**, **Service Workers**, and **Cache Storage**. The `savvy-shell-*` cache should contain app assets only, with no `/api/` entries or source workbook.

Start in **Follow tournament** to set up an event with unknown details and review incoming standings. See [the player workflow](player-workflow.md). Use **Run tournament** to create a local event, review and issue pairings, and enter results; see [the local director workflow](run-tournament.md). **Simulation / Replay** remains a secondary read-only experiment for historical fixtures, including the original XLSX. On narrow screens it is available through **Rules → Open replay lab**. Source gaps and unconfirmed director preferences remain visible; see [the sample data and replay scope](sample-data.md).

The source selector also offers the dated **Official US Chess validation snapshot** from event 202609070433. It is captured from the official rendered crosstable, which includes results and colors, and is kept separate from the original workbook. This bundled snapshot is not a live connector or an automatic refresh of the JavaScript-based ratings site. Unknown availability announcements and unconfirmed pairing setup remain explicit assumptions.

For a clean development cache, unregister this app's service worker and remove its `savvy-shell-*` caches in developer tools, then reload. Bump `CACHE_NAME` in `web/sw.js` when releasing a shell revision. A replacement worker activates after existing tabs using the old worker close; it does not force a reload while someone is editing results.

## Access from an Apple or Android device

The default server is deliberately loopback-only. `127.0.0.1` on a phone refers to that phone, not the computer running Python. A LAN URL such as `http://192.168.1.10:8000` does not satisfy the HTTPS requirement for a full PWA installation. Use a reachable HTTPS origin with the frontend and backend under the same origin. [Service workers require a secure context, with a localhost development exception.](https://developer.mozilla.org/en-US/docs/Web/API/Service_Worker_API/Using_Service_Workers)

The server supports an explicitly configured external HTTPS origin and a private tournament access key. This prepares the application to run behind an HTTPS reverse proxy; it does not provision hosting or a certificate. An example setup, after choosing and configuring your own HTTPS endpoint:

```powershell
python -m app.server --init-access-key
python -m app.server --public-origin https://pairings.example.org --access-key-file .local/access-key
```

Keep Python on loopback when the HTTPS proxy runs on the same host. The proxy must forward the configured public `Host` and route both the shell and `/api/` requests to the Python service. If a container or a separate proxy requires a non-loopback bind, `--host 0.0.0.0` requires both the public origin and the access key. Configure network access for that chosen deployment. Do not disable Host or Origin validation to make mobile access work.

Read the generated key from its private file when entering it on a device. The initialization command does not print the secret. Share the key only with directors authorized to use that tournament workspace. The service currently owns one shared tournament section; this is not a multi-tenant account system. All connected directors see and edit the same server-side workspace. Browser snapshots stay on the device that stored them.

The US Chess Python engine can run on platforms supporting the project's Python requirements. The current pinned BBP Dutch dependency is a **Windows x64 executable**, so that profile needs the configured Windows backend until another verified backend package is supplied. Installing the PWA on a Mac, iPhone, or Android device does not change the backend engine's platform requirement. See [Dutch engine provenance and installation](dutch-engine.md).

No public endpoint or app store listing is created by these repository changes. Responsive desktop browser checks do not substitute for physical-device Safari and Chrome testing. Before distributing an HTTPS deployment, verify installation, authentication, source upload, export/download handling, navigation with the keyboard open, safe-area insets, and reconnection on the target devices.

## Native app and fully offline options

If app store distribution or native device integration becomes necessary, [Capacitor can wrap an existing web application](https://capacitorjs.com/docs). That would reuse this interface while retaining the same API boundary. Native build and signing setup is additional work; [iOS builds require macOS and Xcode, and Android builds require the Android toolchain](https://capacitorjs.com/docs/getting-started/environment-setup). This repository has not built or tested those native targets.

Fully offline forecasting is a separate engine port. [Pyodide supports Python in a web worker](https://pyodide.org/en/stable/usage/webworker.html), which could host selected pure Python code, but cannot execute this Windows BBP binary. A browser engine would need independent correctness fixtures, bounded execution, package and storage design, and an appropriate FIDE engine port before promising equivalent offline predictions.

## App assets

`web/manifest.webmanifest` declares a stable app ID, root scope, standalone display, and 192 / 512 pixel PNG icons. The separate maskable 512 pixel icon keeps the rook within the central safe circle. The 180 pixel Apple touch icon and SVG favicon use the same palette. The icons are reproducible from code with no external font or image service:

```powershell
python scripts/generate_app_icons.py
```

The manifest and icon conventions follow [the web app manifest guide](https://web.dev/learn/pwa/web-app-manifest). The interface preserves the desktop workspace and adds 44 pixel or larger interactive controls, 16 pixel form text, horizontal table scrolling, and a six-item bottom navigation bar on small screens.
