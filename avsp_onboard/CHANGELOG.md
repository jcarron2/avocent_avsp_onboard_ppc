# Companion Daemon (avsp_client_ppc) — Release Notes

Releases are identified by the build fingerprint (`fp`, shown on the viewer and
the `/update` settings page) plus the build date. `fp` changes only when the
source actually changes.

---

## 2026-10-27 — fp `8146771c`   (current)

Supersedes the build deployed on `.99`/field units (**fp `3767ebf7`**,
2026-08-24). Everything below is new since that build.

### New (viewer)
- **Decoded video mode now also shows in the connect popup** — a "video mode"
  row next to "video channel", e.g. `#49 (1280 x 1024 60Hz, DMT1260) — sync:
  separate H/V (+H +V)` (the status-bar line stays too, for after the popup
  auto-hides).
- **Mode reference table** at **`/modes`** (linked from the settings page) —
  the full built-in mode table (all 108 entries): #, name/res/refresh/monitor,
  sync type (SOG / separate / composite, colour-coded), and H/V polarity.
- **Settings page: dropped the redundant "Current build" row.** It showed
  `BUILD_VERSION`, which (now that it auto-tracks the build date) was just the
  date half of the "Built" timestamp below it. "Built" + "Fingerprint" remain.
- **Settings page: "View log" button restyled** to a light neon-blue "pop" (was the muted ghost style).
- **Settings page: mode reference table link** restyled as a neon-blue pill (was easy to miss).

### Fixed
- **Video status line now decodes the real mode.** The `-32250` notice's
  `video_mode` field is `index | 0x7000` (a fixed tag, confirmed in main_app at
  `0x100ef07c`), not a bare index — so the old build showed a meaningless
  "video mode #28694". Now we strip the tag and resolve it to the VESA/monitor
  name **plus the sync protocol** (sync-on-green / separate H-V / composite) and
  **H/V polarity** — e.g. `input 1024x768 — mode #22 (1024 x 768 60Hz,
  VG901101) — sync: separate H/V (-H -V)`. Also surfaced on `/metrics`
  (`video_mode` / `video_mode_name` / `video_sync` / `video_sync_pol`).
- **RAM % no longer lies.** `ram_pct` excluded reclaimable cache — it read ~92%
  on the 44 MB appliance when real process use was ~46%. Now `(Total − Free −
  Buffers − Cached)/Total`; `/metrics` also gains `mem_buffers_kb` /
  `mem_cached_kb`.
- **Settings page shows the real build date.** `BUILD_VERSION` was a hardcoded
  `"2026-08-24"` string that was never bumped, so "Running now" and "Staged
  build" looked identical by date and only `fp` distinguished them. Now injected
  by build.sh as the actual build date.
- **`/update` reachable while a KVM session is active.** A second connection
  (e.g. opening the settings page in another tab) got a ~20 ms request-peek that
  starved under video load, so `GET /update` fell through to serving the **KVM
  viewer HTML** instead of the settings page until you kicked the session. The
  peek now has a realistic deadline (~300 ms, breaking the instant headers
  complete), so `/update`, `/metrics`, `/logs` resolve reliably with a session
  live. (This is the "had to kick the session to reach /update" issue.)

### New
- **Keyboard auto-release on client disconnect.** The daemon now tracks which
  keys the browser has down and releases them (plus all 8 modifiers) when the
  browser connection drops — so a tab crash / network drop mid-keystroke can no
  longer leave a key stuck in the target's virtual USB HID.
- **Second "Reset Keyboard (full)" button.** The existing Reset Keyboard button
  is unchanged (this tab's tracked keys + modifiers) and now also pops the
  status overlay. The new button does an unconditional sweep of **all 232 USB
  keycodes**, paced ~10 ms each (~2.3 s) with a live **"X of 232"** progress
  count — for clearing a key left stuck by a *different* / crashed client. (A
  deep dongle-HID wedge still needs a physical DSRIQ-USB re-seat; the overlay
  says so.)
- **"KVM drive tooling in use" banner.** When the automation driver
  (`kvm_drive.py`) is actively driving this channel, it heartbeats the daemon
  (`GET /drive-notice?on=1`) and the viewer shows a banner: *"⚠ KVM drive
  tooling in use — check if a Claude job is running."* Auto-clears ~20 s after
  the tooling stops (TTL — a crashed driver can't leave it stuck). Shared
  viewing is intentional; this just makes lag/contention self-explaining.

### Tooling (host-side staging/serving — NOT flashed to the device)
- **`mini_dhcpd.py`** — a dependency-free DHCP server that is part of the field
  kit's **staging/serving** side. It runs on the deploy laptop to hand an address
  to a virgin appliance on a field LAN that has no DHCP of its own (companion to
  `mini_tftpd.py`), and is wired into `runme.sh`. Like the TFTP server and
  `deploy_bundle.py`, it is host-side tooling — it is never part of the flashed
  firmware.

### Not a daemon change (for the record)
- The "backing something out" from this round was host-side only: `kvm_drive.py`
  briefly refused to connect to a channel already in use, then that refusal was
  reverted so shared viewing works. The daemon never had that behavior.

---

## 2026-08-24 — fp `3767ebf7`   (previous, as deployed on .99)
- Video-mode/sync tables present but the status line showed the raw
  `video_mode` value (the "#28694" bug, fixed above).
- `ram_pct` counted cache as used (the "92%" reading).
- `BUILD_VERSION` hardcoded to this date.
- Baseline viewer: Reset Keyboard (single), held-modifier auto-release on
  blur/visibility, Signal Guard, Adjust-video, settings/update page, conshell,
  metrics/logs.
