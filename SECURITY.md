# Security

WifiManager runs a small background service **with administrator rights** that changes firewall rules. That deserves a
clear threat model.

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

Use GitHub's private reporting: open the **Security** tab of the repository and choose **Report a vulnerability**. If you
cannot, write to **contact@joselma.online**. Do not put exploit details in a public place.

Include, if you can: the version or commit, the steps to reproduce, what you expected, what happened, and the impact you see.

## Threat model

### What is protected

- Only the **Windows user who started the service** can talk to it: the local channel (a named pipe) is created with
  user-only access.
- The window runs **without administrator rights**. Only the service can touch the firewall.
- Requests are validated: a MAC address, a duration (60 s to 7 days), a mode, a known action. Anything else is refused
  with a message and never reaches the firewall.
- The service **never blocks** the PC's own address on the hotspot network, a public address, a loopback or multicast
  address. It only blocks private IPv4 addresses of devices connected to the hotspot.
- A read error **never unblocks anyone**: the blocks in place are kept.
- Blocks live in a **dynamic firewall session**: if the service stops, every block disappears. Nothing can stay blocked by
  mistake, and a bug cannot lock the user out.
- The Wi-Fi password is never written in clear: it is kept with **DPAPI**, readable only by the same Windows account on the
  same machine. It is passed to PowerShell through environment variables, never on a command line.
- Saved state is written atomically, and a damaged file is ignored rather than trusted.

### What is not protected

- **Any program running as your user** can talk to the service. It can then block or allow devices, and read the device
  list and data counters. The service does not defend against code that already runs under your account.
- **Windows stores the hotspot password in a registry value readable by any program of the session.** This is Windows'
  own behaviour, not something this app adds, but you should know it.
- A blocked device stays associated with the Wi-Fi; only its Internet access is cut.
- Blocking is by IP address. A device that changes address is blocked again at the next reading (every 5 seconds).
- Anyone with administrator rights on the PC can stop the service or edit its scheduled task.
- The service state file (rules, data counters) is not encrypted.

## Scope

In scope: the service and its local channel, the firewall logic, the handling of the Wi-Fi password (DPAPI, environment
variables), the scheduled-task installation, and request validation.

Out of scope: an attacker who already has administrator rights, vulnerabilities in dependencies that WifiManager does not
trigger, and the behaviour of Windows itself (such as how it stores the hotspot password).
