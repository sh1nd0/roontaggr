# Changelog

All notable changes to Fly Me To The Roon. Newest first.

## 1.5.1 — 2026-05-24
- Fixed: dropping a folder whose tracks live in subfolders (e.g. `Album/CD1/`,
  `Album/CD2/`, `Album/CD3/`) now produces a single album with all its tracks
  instead of one album per subfolder. Rule is now "one dropped folder = one
  album, recursing into all subfolders"; drop multiple folders to get one
  album per folder.

## 1.5.0 — 2026-05-24
- Rebrand: the app is now **Fly Me To The Roon**. The macOS bundle, window
  title, About dialog, log file, and update flow all reflect the new name.
  Bundle identifier moves to `com.mark.flymetotheroon`.
- New icon: a paper airplane carrying a music note on a Roon-style violet
  gradient — generated freshly in `make_roontag_app.sh`.
- UI refresh: warm paper-white background, violet primary accent, serif
  display font for the brand wordmark, refined ttk button/entry/treeview
  styling, more generous toolbar + card padding. No layout changes — same
  workflow, brighter feel.
- GitHub releases move to **sh1nd0/roontaggr**; the in-app update checker
  and `publish.sh` point at the new repo.

## 1.4.5 — 2026-04-22
- Changed: the misnamed-audio warning dialog now builds a copy-paste-ready
  ffmpeg command per bad file, using the file's real absolute path and a
  "-FIXED" sibling as the output. No more editing placeholders by hand —
  triple-click the line and paste into Terminal.

## 1.4.4 — 2026-04-22
- Changed: friendlier wording in the Check for Updates dialog when this
  Mac is ahead of the published release. No more "build" / "GitHub
  Release" jargon — just "this Mac is already on the newest version
  (X.Y.Z). Run `bash publish.sh` to share it with your other Macs."

## 1.4.3 — 2026-04-22
- Fixed: Check for Updates no longer says "You're up to date" when the
  running version is actually *newer* than the latest GitHub Release.
  Now distinguishes three cases: up-to-date, ahead of release channel
  (dev build), or update available.

## 1.4.2 — 2026-04-22
- Fixed: `CERTIFICATE_VERIFY_FAILED` error when checking for updates (and
  when fetching metadata/artwork) in the packaged .app. PyInstaller
  builds can't find the system keychain on macOS, so urllib's default
  context couldn't validate GitHub's certificate. Now forces certifi's
  bundled CA store as the default for all HTTPS requests.

## 1.4.1 — 2026-04-22
- Added: container sniffing at ingestion. When you drop in an audio file
  whose extension lies about its content (e.g. a YouTube download that's
  actually Opus-in-WebM renamed to `.mp3`), RoonTag now pops a warning
  dialog explaining what it actually is and giving you the `ffmpeg`
  command to transcode it properly. Detects MP3, FLAC, AIFF, WAV, M4A,
  WebM, and Ogg containers.

## 1.4.0 — 2026-04-22
- Redesigned UI. Toolbar is now grouped into intake (Add Files / Add
  Folder) on the left and action (Fetch / Process) on the right. The
  title bar is cleaner, the detail pane is organized into labelled cards
  (Metadata, Album Type, Tracklist, Tracks) with clearer hierarchy.
- Added: a native macOS menu bar at the top of the screen with File,
  Tags, and Settings menus. About and Check for Updates now live under
  the RoonTag app menu. Keyboard shortcuts: Cmd+O (Add Files),
  Cmd+Shift+O (Add Folder), Cmd+F (Fetch), Cmd+S (Save Changes),
  Cmd+Return (Process All).
- Added: tracklist-to-lyrics for single-file compilations and live
  albums. Flagging **any** of DJ Mix / Live Album / Compilation now
  reveals the Tracklist field. Whatever you type there is embedded into
  USLT lyrics on MP3, FLAC, AIFF, and M4A — so Roon can read the
  tracklist off any single-file compilation or live set.
- Fixed: Check for Updates no longer says "couldn't reach GitHub" when
  the real situation is "no releases published yet." The dialog now
  distinguishes between up-to-date, no-releases, and network errors,
  and shows the relevant information with next-step guidance.
- Fixed: tracklist embedding now works in AIFF and M4A files too (was
  previously only MP3 and FLAC).

## 1.3.0 — 2026-04-22
- Added: in-app auto-update. On launch, RoonTag quietly checks GitHub
  Releases for a newer version and offers a one-click install. No more
  Terminal needed on the non-dev Macs.
- Added: `publish.sh` — on the dev Mac, a single command builds the app,
  zips it, and publishes a new GitHub Release with the zip attached.
- Changed: distribution model. The "dev" Mac builds + publishes; the
  other Macs just download the pre-built `.app` from GitHub Releases and
  get updates in-app.

## 1.2.0 — 2026-04-22
- Fixed: spaces no longer disappear when editing the Title field. The
  KeyRelease handler was stripping and re-writing the StringVar on every
  keystroke, which ate trailing spaces.
- Added: in-app About dialog (toolbar button) showing current version
  plus recent changelog entries.
- Added: versioning pipeline — a single `VERSION` file is the source of
  truth. `build_app.sh` reads it and stamps it into the bundle's
  `CFBundleVersion` / `CFBundleShortVersionString`.

## 1.1 — pre-2026-04-22
- Baseline feature set: drag-and-drop tagging, artwork paste/fetch,
  DJ-mix tracklist support, Roon-friendly TPE1/TPE2/TDRC handling.
