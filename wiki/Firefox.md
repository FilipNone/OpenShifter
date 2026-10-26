Firefox and the browsers built on it (Zen, LibreWolf, Floorp, Waterfox) cannot reach USB devices, so the web tool does not work in them out of the box. The free add-on **[WebHID for Firefox](https://github.com/Sainan/WebHID-for-Firefox)** adds this, with the help of a small program that runs on your computer.

**The easy way is still Chrome, Edge or Opera.** Use this page only if you want to stay in Firefox.

## Read this first

- The add-on and its helper program are made by someone else. They have not been tested with OpenShifter.
- **Firmware updates do not work in Firefox.** Use Firefox for calibration and settings only, and install firmware with Chrome, Edge or Opera. If you started an update in Firefox, the shifter waits in update mode: open the web tool in Chrome, Edge or Opera, connect the shifter and install again from the **Firmware** tab.
- **Firefox shows an error when you change a setting.** This is normal: the setting still changes.
- **On Linux and macOS, the helper does not ask which website may use it.** While it runs, any website you open could talk to your USB game controllers. Run it only while you use the web tool, then stop it.
- On Windows, the helper asks the first time a website wants to use it. Answer **Yes** only for `filipnone.github.io`.
- On Linux the helper also accepts connections from other computers on your network. The Linux steps below block that.

## 1. Install the add-on

Open [WebHID for Firefox on addons.mozilla.org](https://addons.mozilla.org/en-GB/firefox/addon/webhid-for-firefox/) and press **Add to Firefox**.

## 2. Download the helper

Open the [helper's releases](https://github.com/Sainan/WebHID-for-Firefox/releases/latest) and download the file for your computer, then unzip it:

| Computer | File |
|---|---|
| Windows | `Windows.X64.zip` |
| Mac with Apple chip (M1 and newer) | `MacOS.ARM64.zip` |
| Mac with Intel chip | `MacOS.X64.zip` |
| Linux (64-bit PC) | `Ubuntu.22.04+.X64.zip` |

## 3. Start the helper

The helper has no window. Once started, it waits quietly in the background.

### Windows

Double-click the program from the zip. To start it every time you log in, press **Win + R**, type `shell:startup`, press Enter and copy the program into the folder that opens.

### macOS

Run this once in Terminal, then start the program from the zip:

```sh
echo "127.0.0.1 127-0-0-1.faketls.com" | sudo tee -a /etc/hosts
```

Stop it from **Activity Monitor** when you are done.

### Linux

1. Install the udev rule from [Install](Install#before-you-start). The helper needs it to reach the shifter.
2. Copy the program into place and create a service that only accepts connections from this computer. Run this in the folder where you unzipped it:

   ```sh
   sudo install -D -m 0755 WebHID-for-Firefox-Server /usr/local/libexec/webhid-for-firefox/WebHID-for-Firefox-Server
   sudo tee /etc/systemd/system/webhid-for-firefox.service >/dev/null <<EOF
   [Unit]
   Description=WebHID for Firefox helper

   [Service]
   User=$USER
   ExecStart=/usr/local/libexec/webhid-for-firefox/WebHID-for-Firefox-Server
   # Only this computer may connect.
   IPAddressDeny=any
   IPAddressAllow=localhost
   NoNewPrivileges=yes
   ProtectSystem=strict
   ProtectHome=yes
   PrivateTmp=yes
   EOF
   sudo systemctl daemon-reload
   ```

3. Start it before you use the web tool, and stop it afterwards:

   ```sh
   sudo systemctl start webhid-for-firefox    # start
   sudo systemctl stop webhid-for-firefox     # stop
   ```

## 4. If you use uBlock Origin

uBlock Origin blocks the add-on from reaching the helper. Open the uBlock Origin dashboard, go to **My filters**, add this line and press **Apply changes**:

```
@@|wss://127-0-0-1.faketls.com:33881/r1|$websocket
```

## 5. Connect the shifter

1. Open the **[web tool](https://filipnone.github.io/OpenShifter/)** and press **Connect the shifter**.
2. Firefox asks whether the page may access other apps and services on your device. Press **Allow**.
3. In the list, pick **VelocityOne Multi-Shift** and press **Connect**.

If the list is empty, check that the helper is running and, on Linux, that the udev rule is installed.
