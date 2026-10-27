# SSH Workbench

A Python desktop application for SSH terminals and SFTP directory browsing, inspired by the basic workflow of Royal TSX. It is a separate tool: it does not read the CEM configuration, change CEM applications, install anything on the Linux server or start background remote commands by itself.

This appearance update follows the supplied terminal/file-browser reference: charcoal panels, blue accents, alternating file rows, blue folder icons, a visible permissions column and a larger terminal pane. Use **Connections** in the top-right corner to show or hide saved profiles and drag the divider to resize the file browser. The explicit palette keeps text readable across operating-system appearance settings. SSH, authentication, file operations and saved profile storage are unchanged.

## Features

- Saved connection profiles: name, hostname/IP, SSH port, username, authentication method, private-key path and initial SFTP directory.
- Multiple independent sessions in tabs.
- Clickable remote-folder breadcrumbs, Root/Home/Up navigation, directory double-click and Refresh.
- Sort by Name, Size, Modified date or Permissions using column headers; click again to reverse.
- Create, edit and save remote UTF-8 text files in a dedicated editor (up to 1 MiB).
- Interactive SSH shell: type commands directly, use Enter, arrows, Tab and Ctrl+C. `sudo` password prompts can be answered in the terminal when the server permits the selected authentication method.
- Basic monochrome VT100 emulation and up to 1,000 lines of in-memory scrollback.
- Separate SFTP browser: navigate by path or double-click directories, go up, inspect file size/date/permissions, preview text, upload and download.
- Password, private-key/passphrase and SSH-agent/local-key authentication.
- New-host fingerprint confirmation and changed-host-key rejection.
- Background network workers so SSH/SFTP operations do not intentionally block the UI.

## Grouped right-click menu, ZIP archives and shell scripts

Right-click actions are now grouped as follows. The clicked row remains the target after sorting/filtering. Empty-space clicks clear selection; inapplicable actions are disabled.

| Group | Actions |
| --- | --- |
| Open / Edit | Open folder, View file, Edit file, Tail log / follow |
| Create | New file, New folder |
| Transfer | Download, Upload file, Send to server (SCP) |
| Archive | Create ZIP, Extract ZIP |
| Tools | Run shell script, Open terminal here, SHA-256 checksum, Properties |
| Copy | Copy path (or current folder path), Copy name |
| Manage | Rename, Permissions, Delete file/folder |

Refresh stays directly accessible. Existing editing conflict checks, deletion confirmations, transfers, and SSH host-key checks are retained.

**Create ZIP:** select a regular remote file/folder and choose Archive → Create ZIP. Enter a new `.zip` filename, saved beside the selected item. Folders include the top-level folder, hidden files and empty subfolders. Existing destinations are refused. Symlinks, devices, unreadable files and changing files fail the operation rather than silently omitting entries. A temporary ZIP is published without replacing existing files. The destination filesystem must support hard links; otherwise publication fails without overwriting files.

**Extract ZIP:** select a regular `.zip` file and choose Archive → Extract ZIP. A new destination folder beside the archive is required; the default is `<archive>_extracted`. Existing folders are never merged or overwritten. Extraction rejects traversal, absolute/Windows-style unsafe paths, duplicate members, symlinks, special files and password-protected entries. Ordinary file permission bits, including executable bits, are retained; special permission bits are stripped. New folders are private to the SSH user. Ownership, timestamps, ACLs and original directory permissions are not restored. On failure, partial files can remain in the new destination folder; the error identifies it.

Archive operations require **Python 3.6+ and standard zipfile/zlib support on the remote Linux server**. No remote package installation or sudo is performed. Data is processed remotely, not downloaded into application memory. Limits: 100,000 entries, 10 GiB of uncompressed input/output and a ten-minute timeout. Work runs off the UI thread. Disconnect closes the request but does not guarantee remote process termination; refresh before retrying because partial output or a hidden temporary ZIP may remain.

**Run shell script:** select a regular `.sh` file, choose Tools → Run shell script, select `bash` or `sh`, and optionally enter arguments. The dialog previews the command before launch. Values with spaces can be quoted; shell substitutions/operators are passed as literal arguments, not executed as extra commands. The selection is rechecked for changes before launch. Execution uses the current SSH user and the script's directory in the existing interactive terminal. Ensure the terminal is at a shell prompt. Output and prompts appear there; Send Ctrl+C can interrupt a foreground job. Script changes are not rolled back. No automatic chmod or sudo is used. The chosen interpreter must exist on the server; refresh files after completion.

**Open terminal here** reveals the terminal and changes to the selected folder, or current browser folder for files/empty space. Use it only at a shell prompt. **Properties** fetches fresh SFTP permissions, UID/GID, size, local-time modification timestamp and symlink target. Folder size is metadata size, not recursive contents. **SHA-256** streams a regular file via SFTP with progress, checks metadata for changes, and offers Copy checksum. **Copy name** copies the selected name without its path.

### Update and validation

Changed runtime files: **app.py**, **icons.py**, and the new **file_actions.py**. Copy all three to the existing source directory and restart. Requirements and saved profile storage are unchanged. If launching a packaged `.app` or `.exe`, rebuild using the existing build instructions. This delivery contains updated source, not a freshly compiled executable.

Validation: 165 tests run; 164 passed and one environment-dependent test skipped. Coverage includes existing behavior, grouped menus and targeting after sorting, script acceptance/cancellation and quoting, ZIP round trips, overwrite refusal, traversal/symlink rejection, limits, stale selections, checksums and properties. Native Qt menus were rendered offscreen for inspection. Live SSH hosts and packaged macOS/Windows executables were not tested.

## Install and run on your Mac/workstation

Use Python 3.10 or newer on an operating system supported by the current Qt/PySide6 packages. This is a **desktop application**, not a server-hosted browser page. Do not run it using the CEM server's Python 3.6 environment. If pip reports that no compatible PySide6 wheel is available, check your Python and OS versions; send that error before changing any system packages.

```bash
unzip SSH-Workbench-Server-Time.zip
cd ssh-workbench
python3.12 --version
python3.12 -m venv .venv312
source .venv312/bin/activate
python3 -m pip install -r requirements.txt
python3 app.py
```

It opens a native desktop window. There is no application port, web server or browser tunnel to configure. For later launches:

```bash
cd /path/to/ssh-workbench
source .venv312/bin/activate
python3 app.py
```

Windows users can activate with `.venv\Scripts\activate` and use `python app.py`.

## Connect to a Linux server

1. Click **Add connection**.
2. Enter a display name, the server's resolvable hostname/IP, port (normally 22), and your authorized username.
3. Select **Password**, **Private key file**, or **SSH agent / local keys**. Private key mode also requires a path to the key on this workstation.
4. Set the initial directory, for example `/var/log`. This controls the file browser; it does not automatically run `cd` in the terminal.
5. Save, select the profile and click **Open session** (or double-click it).
6. Click **Connect** in the session tab. Supply the password or key passphrase when requested. Passphrases are not saved in the profile.
7. If the host key is unknown, compare the displayed SHA256 fingerprint with a trusted administrator/source before accepting. A changed key is blocked; the tool does not silently replace an existing trusted key.

Your workstation must have the normal network/VPN access to the target SSH port. The app does not bypass corporate routing, access controls or login restrictions.

## Terminal and files

Type commands in the dark terminal pane. This is a persistent remote shell, so shell state such as `cd` persists within that tab. Commands execute with the connected user's actual permissions. No AI generates or runs commands in this app.

Example commands you can enter after connecting:

```bash
hostname
pwd
cd /var/log
ls -lh
tail -n 100 /var/log/YOUR_APPLICATION.log
```

Use **Send Ctrl+C** to send an interrupt even when you have selected terminal text. Use **Disconnect** to close the SSH session. Disconnecting is not a guarantee that backgrounded, detached or `nohup` remote processes will stop.

Copy selected text with your normal copy shortcut or right-click menu; paste uses the normal paste shortcut/right-click menu. A multiline paste asks for confirmation because it can execute multiple commands. Paste size is limited to 200 KB. Some advanced keyboard mappings, colors, alternate-screen behavior, full-screen editors and TUI applications may not behave like a mature terminal emulator; use your existing terminal for complex TUI work until validated.

The SFTP pane and terminal working directories are independent. Typing `cd` in the shell does not change the file browser, and browsing a directory does not run a command in the shell.

- **Go / Up / directory double-click:** browse remote directories, limited to 5,000 displayed entries per listing.
- **View file:** read-only UTF-8 preview, capped at 256 KB. Binary and special-device files are rejected. Directory symlinks can be opened; for file symlinks use View/Download.
- **Download:** choose a local destination. A temporary file is written with mode 0600 and replaces the destination only after the transfer completes. No file is automatically opened or executed.
- **Upload:** choose a local file and confirm the remote destination. An existing destination may be replaced. Uploads stage a temporary file, preserve the local file's permission bits and use the server's atomic POSIX rename extension. If unsupported, the upload reports an error instead of deleting an old destination as a fallback. Refresh the directory with Go after upload.
- Closing a session or disconnecting cancels its active transfers. A dropped connection may leave a hidden remote temporary upload file if cleanup cannot reach the server.

## Clickable navigation, sorting and remote editing

- Double-click a directory to enter it. Use **Root /**, **Home ~**, **Up**, or click an ancestor breadcrumb, for example **Root / → bppm → opt**. Select a folder and click **Go** (or press Enter) to enter it. If you edit the path box, Go opens that typed path instead. Use **Refresh** to reload the directory without entering a selection. **Up** uses the currently displayed directory, even if the path box contains unsubmitted text.
- Click **Name** to sort alphabetically, or **Modified** to sort by modification time. Click again to reverse the order. Size is sorted numerically. Sorting is applied to the displayed listing (maximum 5,000 entries), and persists across refreshes within the session.
- **New file…** creates a UTF-8 file in the directory currently displayed, after you enter a file name, type its contents and click **Save to server**. It refuses to overwrite an existing file with the same name.
- Select a regular text file and click **Edit file…** to open the full file in an editor. Click **Save to server** to update it. **View file** remains a separate read-only preview.
- Closing an editor with unsaved changes asks whether to discard them. Saving errors retain your text. Copy it before closing if the connection is lost.
- The editor supports strict UTF-8 regular files up to 1 MiB; binary files, invalid UTF-8, symlinks and special devices are rejected. Uniform CRLF line endings are retained; mixed line endings are normalized to LF. Remote permissions still apply; the editor does not run sudo or elevate privileges.
- Saves stage an exclusive temporary file and replace the target using the server's atomic POSIX rename extension. If unsupported, the save fails without a delete-and-recreate fallback. New files use a no-overwrite rename and mode 0600. Existing POSIX ownership and permission bits are retained or the operation fails.
- Existing files are checked for content/metadata changes before replacement. A detected conflict blocks saving; close and reopen the file to load the current version. SFTP does not provide compare-and-swap locking, so this is best-effort conflict detection: avoid editing actively written files.
- Atomic replacement creates a new inode. ACLs, extended attributes, custom SELinux labels and hard-link relationships are not preserved by this SFTP editor. Use the established server-side editing process for files that depend on those attributes.
- No application/service restart is performed after saving. Refreshing the file browser does not change the shell's working directory.

## Compact workspace and connection groups

The remote file browser fills each session by default. Click **Show terminal** in the session header when you want the right-side shell, or **Hide terminal** to reclaim the space. Each session has its own toggle. Hiding the terminal keeps its SSH shell connected and continues receiving its bounded scrollback; it does not stop commands that are already running. When shown, the initial file/terminal split is about 60/40 and can be resized by dragging the divider.

Navigation buttons are compact 28×26 logical pixels with descriptive tooltips, file icons are 14×14 and file rows are 26 pixels high. Controls retain normal macOS/Windows display scaling. Tabs use rounded styling, a compact close button, drag-to-reorder and overflow scrolling.

Set **Connection group** when adding or editing a profile. Choose **Dev**, **Cert**, **Prod** or type a nested path (up to eight levels, 80 characters per name). The saved connections sidebar groups profiles under expandable headings and searches group names as well as connection details.

Existing profiles without a group appear under **Ungrouped** automatically. To move existing connections, select their rows (Command/Ctrl-click for several) and click **Move to group…**, then choose or enter a group name. Group changes preserve connection IDs and credentials remain excluded from saved profiles. Empty groups and sub-groups can now be created and retained from the right-click menu. Editing a profile affects future sessions; existing session tabs retain the connection settings used when opened.

Select a group heading (or a connection inside it) and click **Rename group**, press **F2**, or right-click and choose **Rename group…**. The dialog edits only the name, not the connection count. Renaming updates every saved member, even when the search filter hides some members. Blank/invalid names and existing group names are refused; use Move to group to merge connections deliberately. Cancelling or a failed save leaves the stored groups unchanged.

File actions now live in the right-click menu, with matching outline icons. The bottom icon toolbar is hidden, leaving more space for files. Right-click an item to select it and open its available actions. Right-click empty space for folder-level actions. Navigation, server/group rows, session actions and the application header retain the same icon style; no icon fonts or network assets are required.

`preview.png` shows the full-width file workspace; `preview-terminal.png` shows the optional terminal. Both contain fictional example data.

## Navigation controls and shortcuts

| Control | Action | Shortcut while the file panel has focus |
| --- | --- | --- |
| ↑ Up | Open the parent of the displayed folder | Alt+Up |
| ↓ Open | Enter the selected directory; does not try to open regular files as folders | Alt+Down |
| ← Back | Return to the previous successfully opened folder | Alt+Left |
| → Forward | Move forward through navigation history | Alt+Right |
| Refresh | Reload the displayed folder, ignoring unsubmitted path edits | F5 |
| Path box | Focus the path and select its text | Ctrl+L / Command+L |
| Copy path | Copy the selected entry's absolute path, or the current folder when none is selected | Button |

Navigation history holds up to 200 locations per session. A failed request does not advance it. Opening a new location after going Back removes the unused Forward branch. Refreshing the same location does not add a duplicate.

The case-insensitive **Filter files in this folder…** box filters the loaded listing locally, without issuing additional SSH requests or recursively scanning the server. It stays active across folder navigation and sorting; clear it with the × button to show all loaded entries. The status displays the visible and total entry counts. Hidden entries cannot be used as the selected target for file actions.

Shortcuts are scoped to the remote file panel. They do not override the terminal's keyboard handling.

## Delete a remote file

Right-click a file and choose **Delete file…**. The confirmation shows the full remote path and defaults to Cancel. Confirm **Delete file** only when you intend to remove it permanently; the app does not provide undo or a recycle bin. The displayed folder refreshes after successful deletion.

Deletion uses SFTP with the connected user's permissions and supports one regular file or symlink at a time. A symlink is removed without deleting its target. Folder deletion uses the separate confirmed workflow below; special devices are refused and no sudo is used. The selected entry is tied to its path even after sorting/filtering. A metadata recheck blocks deletion if its type, size, modification time or permissions have changed since listing; this is a best-effort check, not a server-side lock. Disconnect cancellation and permission failures are reported without showing a successful deletion.

## Change remote Linux permissions

Right-click one regular file or folder and choose **Permissions…**. The dialog fetches its current Unix mode and shows its full path, symbolic/octal permissions, owner UID and group GID.

Choose **Read**, **Write**, **Execute / traverse** for **Owner**, **Group** and **Others**, or type a three/four-digit octal value such as `644`, `0755` or `1777`. The checkboxes and numeric field stay in sync. Existing set-UID, set-GID and sticky bits are visible and retained unless you change them; a three-digit mode explicitly clears special bits. Review the displayed current-to-new mode and click **Apply permissions** to submit it. Closing without applying makes no remote changes. Close the dialog to refresh the file listing.

Changes use the connected account's SFTP chmod privileges. The app does not use sudo, change ownership or apply recursively. Server permission errors are shown in the dialog. Symlinks and special devices are refused. Permissions/ownership are rechecked before applying, and the resulting mode is read back afterward; these are best-effort checks, not a server-side lock. If the chmod succeeds but verification fails, the message states that the request completed and asks you to refresh.

`preview-permissions.png` shows the dialog with fictional sample data. No corporate server was contacted during development. The Windows builder automatically includes the new permissions dialog module when rebuilt.

## Local storage and credentials

- Profiles: `~/.ssh-workbench/profiles.json` (mode 0600).
- App-specific accepted host keys: `~/.ssh-workbench/known_hosts` (mode 0600).
- The local OpenSSH known-hosts file is also read for verification; the app does not write it.
- Passwords/passphrases are held transiently in process memory for login/trust retry and are not intentionally stored in profiles, logs or this delivery. Python cannot guarantee secure zeroing of memory.
- Terminal contents and scrollback are kept in memory, not automatically logged to disk. Downloaded files and copied clipboard text are explicitly controlled by you.
- No real connection profiles, server credentials, CEM configuration or personal data are bundled.

This initial version does not parse `~/.ssh/config`, use ProxyJump/bastions, support RDP/VNC, vault saved passwords, provide session recording or implement generic interactive MFA/SSO challenges. SSH-agent support depends on the keys and agent accessible to the process. Enter a directly reachable hostname/IP rather than assuming an OpenSSH alias will be resolved by this app.

## Validation

```bash
python3 -m unittest discover -s tests -v
```

The development run passed 144 tests (one additional Linux procfs integration check was skipped because procfs is unavailable in the build sandbox), including a real SSH handshake to a synthetic localhost Paramiko server with a verified key and bidirectional terminal traffic. Other tests cover profile credential exclusion, host-key persistence/change rejection, directory handling, preview limits, cancelled downloads, atomic-upload failure behavior, offscreen Qt GUI/key events and overriding an inherited low-contrast OS palette for active/inactive windows. Additional disk-backed SFTP tests cover new-file overwrite refusal, editing conflicts, atomic-save failure, cancellation, mode preservation, strict UTF-8/binary/size rejection, symlinks and CRLF line endings. GUI tests verify sorted file identity, numeric size ordering, breadcrumb destinations and retention of unsaved text on cancellation/save failure.

The native UI was rendered and visually inspected offscreen on Linux. `preview.png` shows fictional sample data, not a connection to your servers. No corporate server was contacted. A real SSH/SFTP session on your Mac, your login method and your target server still needs validation.

Implementation references:

- https://docs.paramiko.org/en/stable/api/client.html
- https://doc.qt.io/qtforpython-6/
- https://pyte.readthedocs.io/en/latest/api.html

## Rebuild the macOS application after this update

Python 3.12 is recommended for a fresh build environment. If `python3.12` is not installed, install it using your usual Python installation method before the setup commands above.

Run from the updated `ssh-workbench` folder using the existing virtual environment (or create it as described above):

```bash
source .venv312/bin/activate
python3 -m pip install -r requirements.txt
python3 -m pip install --upgrade pyinstaller
python3 -m PyInstaller --noconfirm --clean --windowed --onedir \
  --name "SSH Workbench" --distpath dist312 --workpath build312 \
  --collect-all pyte --collect-all wcwidth app.py
open "dist312/SSH Workbench.app"
```

The ZIP contains source code, not a compiled `.app` or `.exe`. Rebuild on the target platform. Share the resulting `.app` with compatible Macs; signing/notarization is still needed for normal trusted distribution. Existing profiles in `~/.ssh-workbench` are retained when replacing the app.

## Windows executable build kit

See **WINDOWS-BUILD.md** for the full Windows instructions. On a Windows x64 machine with Python 3.12, double-click **build_windows.cmd** to build a single executable, or run `py -3.12 build_windows.py --mode onedir` for a portable folder. From a Mac, use the included `.github/workflows/build-windows.yml` on a Windows GitHub Actions runner.

The build kit has not been compiled on Windows in this session. All 90 current tests passed on Linux, including three builder/archive tests, seven deletion regressions and eleven permissions regressions. The Windows workflow configuration was also checked. No Windows executable was built in this session.

## Folder listing compatibility and navigation feedback

Back and Forward are history controls. Forward is disabled until you go Back from a later location. Use the down arrow or double-click to enter the selected directory; use Up to reach its parent.

If the normal SFTP listing raises a UTF-8 decoding error, the app closes that channel and retries using an isolated compatibility reader. Human-readable longname data is ignored; valid UTF-8 filenames remain usable. A filename that is not valid UTF-8 is shown with escaped bytes and a `[non-UTF-8 name]` suffix. Its row is display-only and is excluded from file actions, preventing an incorrectly decoded name from targeting another file. Use the SSH terminal for those particular names. The folder status reports such entries, rather than failing the entire listing or silently hiding them.

The compatibility reader preserves cancellation and the 5,000-entry limit. It uses isolated Paramiko SFTP protocol methods, covered by wire-message regression tests; it does not globally patch the library's encoding rules. Editing non-UTF-8 file contents remains deliberately unsupported. Five additional encoding regressions passed locally; target-server validation is still needed.

## Right-click actions and New Folder

Right-click a row to target that exact item, including after sorting or filtering. Available actions include **Open folder**, **View file**, **Edit file…**, **Download…**, **New folder…**, **New file…**, **Upload file…**, **Permissions…**, **Copy path**, **Refresh**, and **Delete file…**. Actions that do not apply to the item are disabled; for example, folders cannot be edited/downloaded/deleted as files. Delete confirmation and permission validation are retained. A non-UTF-8 display-only name cannot be used as a file-action target.

Right-click empty space to clear the old row selection and create a file/folder, upload, copy the current folder path or refresh. On a Mac, use the secondary-click/two-finger click gesture configured for your mouse or trackpad. The previous bottom action toolbar is hidden by default.

Choose **New folder…**, enter a single folder name, and confirm. It is created inside the **currently displayed directory**, not inside the row you happened to right-click. Open a child directory first if you want to create a folder inside it. The app uses SFTP mkdir with normal server umask/permission rules and refreshes the parent directory afterward. Existing names are refused; no recursive creation, overwriting, shell commands or sudo are used. Invalid names, cancellation and server errors leave the listing available for review.

`preview-context-menu.png` shows the menu with fictional sample data. Seven new folder/menu regressions passed, in addition to the prior checks. The folder encoding fallback and Back/Forward history fixes are included.

## Sortable Type column

The file list displays **Name | Type | Size | Modified | Perm.**. Click **Type** to sort by type, and click again to reverse the direction. Equal types are ordered by filename. Type sorting is retained when refreshing within the same session; file actions continue to target their row's remote path.

Types use directory/link metadata and filename extensions, without reading file contents: **Folder**, **Symbolic link**, **CSV file**, **BAROC file**, **SH file**, **LOG file**, etc. Extensionless files show **File**. Type labels are not content/MIME validation. Non-UTF-8 display-only names retain their action restrictions. Two new regressions verify labels, ascending/descending sorting, column placement and correct right-click targeting after type sorting.

## Delete folders and directory trees

Right-click a directory and choose **Delete folder…**. The confirmation displays its full remote path and defaults to **Cancel**. With **Delete all contents** unchecked, the server removes only an empty folder; a non-empty folder is refused without deleting its contents. Check **Delete all contents, including files and subfolders** to explicitly request recursive deletion. All removals are permanent and there is no recycle bin or undo.

Recursive deletion first reads a bounded deletion plan before mutating files, up to a 50,000-item limit. Symlink entries are unlinked rather than traversed, root deletion is blocked, and path/type/ownership/permission changes are checked. Special-device entries or directory-listing encoding errors abort planning before any removal. The folder-listing UTF-8 fallback remains available for normal browsing; it does not guess paths for recursive deletion.

Deletion runs via SFTP with the connected user's privileges. No shell commands or sudo are used. If deletion stops after starting, the error reports the number of removed entries and warns about possible partial deletion; use Refresh to inspect the result. Successful deletion refreshes the parent automatically. Cancellation cannot restore already removed files.

These SFTP checks are best-effort, not atomic locks: do not change directory paths concurrently with deletion. SFTP does not identify filesystem mount boundaries, so recursive deletion can affect mounted content located beneath the chosen directory. Verify the selected tree before confirming.

`preview-folder-delete.png` shows the new confirmation using fictional data. Eight additional regressions cover folder deletion, symlink target preservation, changing directory paths, cancellation, preflight errors, partial failure and the explicit recursive checkbox. All 144 runnable tests passed locally (one procfs integration test was skipped); native OS and target-server validation is still needed.


## Rename files and folders

Right-click a file or folder and choose **Rename…**. Enter its new name (not a full path). It stays in the same directory, and the listing refreshes on success. Existing names are refused; rename does not replace another item. A metadata recheck catches obvious changes since the listing was loaded. This is best-effort detection, not a server lock. Standard SFTP rename support and normal write permissions on the parent are required.

## Send directly to another server with SCP

1. Connect to the source server and right-click a regular file or folder.
2. Choose **Send to server (SCP)…**.
3. Select a saved destination to fill its hostname, username and port, or enter those details manually. This uses password authentication independently of the profile's usual authentication method.
4. Enter the absolute **Destination path**. For example, use `/tmp/` to copy `nithin.txt` into the existing `/tmp` folder, retaining its name, or `/tmp/nithin-copy.txt` to give it a different destination name. For folders, a trailing `/` targets an existing parent directory.
5. Click **Start transfer** and review the overwrite confirmation.
6. If SCP asks to trust an unknown host, verify the displayed fingerprint independently, then use **Verify host key…** to confirm. This trust entry is saved on the source server under the connected user's SSH known_hosts, separately from the workstation's trust store. Changed keys remain blocked.
7. When prompted, type the destination password in the masked field and click **Send password** (or press Enter). Passwords are sent only to the interactive SCP channel; they are not put in command arguments, saved in connection profiles or deliberately written to the log. The dedicated transfer PTY has echo disabled before SCP starts.
8. Wait for **Transfer completed successfully**, then verify the destination using a separate session. The source remains in place.

The application runs the source server's `scp` command through a separate SSH channel on the existing connection. The source needs `scp` installed, a POSIX-compatible login shell and direct SSH network/VPN routing to the destination; the destination must permit password authentication and access to the requested path. Password-only authentication is supported here, not MFA/keyboard-interactive challenges or keys. Hostnames/IPv4 and conventional Linux usernames are accepted. IPv6 destinations and destination paths containing spaces, non-ASCII characters or shell metacharacters are currently refused for compatibility with older SCP remote shells. SCP selects its normal transfer protocol for its installed version (new versions generally use SFTP).

Existing destination files **may be overwritten** after confirmation. Folder copies are recursive and SCP follows symbolic links encountered within them; review the folder contents before copying. Top-level symlink transfers are disabled. This copy is not an atomic upload and does not promise ACL, extended-attribute or ownership preservation. Cancellation, connection loss or failure can leave partially copied files; inspect and clean up the destination yourself. Five minutes without output/input stops the worker; this includes waiting for a password. Cancelling closes the transfer channel, but cannot guarantee that a detached remote process has stopped. Source changes during the copy are not locked out.

## Compact connection toolbar

The six stacked connection buttons are now one row of small outline icons below the saved connections. Hover for each label: **Add connection**, **Edit selected**, **Rename group**, **Move to group…**, **Open session**, and **Remove profile**. Double-clicking a connection and the existing right-click group/connection menus continue to work. The file browser's right-click menu also includes Rename and Send to server.

`preview-transfer.png` shows the SCP form with fictional data. Local automated tests use fake SSH/SFTP channels; actual corporate-server transfers and native macOS/Windows builds still need environment-specific validation.


## Readable file sizes

The Size column now shows **B, KB, MB, GB, TB** or PB, using 1,024 bytes per KB. Sorting continues to use the exact numeric byte count, not the displayed text; hover to see the precise byte count. Folder sizes describe the directory entry metadata, not the combined contents of the folder. The app does not recursively scan folders to calculate their size.

## System monitor for on-call support

Connect to a server, then click **System monitor** in the session header. Use **Back to files** to return to the normal file browser and optional terminal. The monitor opens inside that server's session and uses the existing trusted SSH connection; no remote Python package or monitoring agent is installed. Opening the monitor explicitly runs read-only Linux commands. Merely creating a profile or opening a disconnected session does not start monitoring.

### Overview

- CPU busy percentage sampled from two `/proc/stat` readings about 0.3 seconds apart. Idle and I/O-wait counters are treated as idle for this display; guest counters are not counted twice.
- A CPU trend of the latest 60 successful overview refreshes. Graph positions represent samples, not a time-scaled historical chart; data is held only in the session's memory.
- RAM used, available, raw free and total. Used RAM is total minus available; available includes reclaimable cache, so it differs from raw free. A tooltip identifies the fallback estimate if MemAvailable is unavailable on an older kernel.
- Swap used/free/total, uptime, host/kernel details, logical CPU count and 1/5/15-minute load averages.
- Local filesystem total/used/available space and usage bars. The `df -P -k -l` query intentionally excludes network filesystems. Reserved filesystem blocks mean total minus used can exceed available space. A disk query failure leaves the other metrics visible with a diagnostic message.

These are host-visible Linux counters, not guaranteed container/cgroup limits. The timestamps use the workstation's local timezone. Snapshots are not monitoring alerts or guaranteed instantaneous readings.

### Processes

The process table displays PID, executable/process name, user, CPU %, RAM %, resident memory (RSS), elapsed time and process state. CPU and RAM columns include percentage bars. Sort using column headers or filter locally by any displayed value. Numeric values sort numerically even when displayed with size units.

CPU % comes from `ps` and is a lifetime average, not the overview's short-interval CPU sample. A process using multiple CPU cores can exceed 100%; the bar fills at 100% while the numeric value retains the actual value. Process command arguments and environment variables are not collected. The first 3,000 processes ranked by CPU are displayed if that limit is reached, with an explicit truncation notice. Visibility depends on the server's permissions and procfs configuration. This screen does not terminate arbitrary processes.

### Services

This view supports **systemd system services**, using `systemctl`. It merges loaded service states with installed unit files. An installed service whose runtime state was not returned is shown as **unknown / not loaded**, rather than assumed to be stopped. Boot setting is informational: enable/disable configuration is not changed. Template units such as `worker@.service` cannot be operated on without a concrete instance.

1. Open **Services** and use the filter to find the exact service.
2. Select its row. **Details** retrieves its current systemd properties, including state, main PID, result, timestamps and available memory/task counters.
3. Click **Start**, **Stop** or **Restart**. Review the confirmation showing the exact host, unit and connected username. Cancel is the default.
4. By default, the action runs as the connected account. The optional **Use pre-authorized sudo** checkbox adds `sudo -n` and requires an existing authorization or suitable passwordless sudo policy. It does not request, store or bypass a sudo password. Services requiring interactive sudo or a different init/service manager must be handled through the existing terminal.
5. A zero exit status means the systemd request was accepted, not that the service has reached its target state. The app uses `--no-block` and reports this distinction. Click **Refresh**, or wait for the next enabled timed refresh, to verify the observed state; further service actions stay disabled until the service list is refreshed.

Actions are restricted to one validated `.service` unit and the three supported operations. Unit names cannot inject shell commands or wildcards. There is no automatic service restart, elevation, action retry, or automatic application health-check claim. A timeout/disconnect can leave the outcome unknown; reconnect and inspect status before retrying. Starting/stopping/restarting a service can affect dependencies and application traffic.

### Refresh, errors and overhead

Auto refresh is **off by default**. Choose every 15, 30 or 60 seconds if required. Only the currently visible monitor tab in the selected session is refreshed. Hidden monitor views, other server tabs and busy monitor workers do not launch overlapping timed queries. Read-only queries use separate bounded SSH channels; existing terminal/file operations stay available. No poll guarantees zero server overhead; each process/service/filesystem query performs ordinary OS work.

Remote output is capped at 2 MiB per command, service rows at 5,000 and ordinary query time at 25 seconds (15 seconds for disk listing), in addition to bounded SSH channel setup. Disconnects/timeouts close the command channel and show a truthful failure; a remote process is not guaranteed to stop merely because its channel closes. Old values remain visibly stale after an error or disconnect. Successful snapshots show their collection timestamp. A failed service refresh disables service actions until a successful service-list refresh.

Linux `/proc`, procps-compatible `ps`, `df` and a POSIX-compatible shell are required for metrics. Service management additionally requires a running systemd system manager, `systemctl` and normal account/policy permissions. Missing commands or access failures are reported. CPU/memory snapshots are validated with fixtures; the sandbox cannot expose host procfs for the additional real-procfs integration test. Native macOS/Windows builds, actual Linux service transitions and corporate-server behavior still require environment-specific validation.

`preview-system.png`, `preview-processes.png` and `preview-services.png` show the new interface using fictional example data.


## Reconnect and connection indicators

The compact session header now uses small icon controls with hover labels and accessible names. In order, these are **Show/hide terminal**, **System monitor / Back to files**, **Connect**, **Reconnect SSH**, **Send Ctrl+C** (only when the terminal is visible), and **Disconnect**. The folder navigation/path controls share one compact row; breadcrumbs and filtering also use smaller controls to leave more vertical room for files.

Click the circular-arrow **Reconnect SSH** icon if the session has disconnected. It closes the old SSH worker, waits for that worker to end, and starts a fresh connection in the same tab. A still-connected session asks for confirmation before replacing its shell. Active file, monitor, SCP or log-read operations must finish first. Password/key-passphrase authentication asks again; secrets are not retained for automatic reconnect. Cancelling authentication leaves the tab disconnected. Disconnect or closing the tab cancels a pending reconnect. The last successful browser folder is restored; a missing or inaccessible folder reports an error and can be replaced using the path box. Browser-folder restoration does not run `cd` in the shell, restore running commands or resume failed transfers.

The session header has a **green** indicator for an established SSH connection, **red** for a disconnected session, and **amber** while connecting/disconnecting. Session tabs and saved server icons reflect the same state. A saved profile is green if any open session for that profile is connected. A red saved profile means this app has no connected session for it; it is not proof that the server itself is down. Status follows the SSH worker's detected state, so silent network outages can take time to detect. Host-key verification and changed-key rejection remain in effect.

The header also shows the session's group/environment badge. A `Prod` or `Production` path component gives it a red tint. This is a visual reminder, not a separate authorization policy; the exact host and username remain visible. An already-open session keeps the group label/settings it had when opened, while changes to saved groups apply to future sessions.

## Nested groups and saved ordering

- Right-click the saved connections tree (including empty space) and choose **New group…** for a top-level group.
- Right-click a group and choose **New sub-group…** to create a child. Empty groups are retained, so they can be created before adding connections.
- Choose a group path in the connection editor or **Move to group…**. You can type `Prod / CEM / Kansas City`; `/` separates levels. Up to eight levels are supported, with 1–80 characters per group name. Previously saved names containing `/` are now interpreted as paths and have separator spacing normalized.
- Right-click a group and use **Move group up** or **Move group down** to reorder it among its siblings. This works for top-level groups and sub-groups. Ordering is saved across application restarts. Moves use the complete sibling order, including groups hidden by search; the hierarchy itself is unchanged. Drag-and-drop reparenting is not implemented.
- **Rename group…** edits the selected leaf name and renames its entire branch, including saved connections hidden by the filter. Existing destination groups are refused. Connection IDs are preserved.
- **Remove empty group…** removes a branch only when it contains no saved connections. Move/remove its connections first if necessary.

Profiles and group ordering are written together atomically to the local configuration. Legacy list-format `profiles.json` files remain readable and are upgraded on the next save to a version-2 object containing `profiles` and `groups`. Before the first legacy upgrade, a credential-filtered `profiles-v1-backup.json` is saved with mode 0600. Existing backup files are retained. Older app versions do not understand the new format; use the legacy backup if deliberately rolling back. Passwords/passphrases remain excluded. Search continues to match full group paths and connection details, keeping the matching ancestry visible.

## Read-only log snapshots for on-call investigations

Right-click a regular file and select **Tail log / follow…**. The viewer loads up to the last **200 lines**, reading at most the final **256 KB** of file contents through `tail -c`. Search filters those loaded lines locally; **Copy visible** copies exactly the displayed text. The viewer does not edit or truncate remote logs.

**Follow · 5s** is off by default. Enable it to refresh a bounded snapshot every five seconds while the window is visible. Only one read runs at a time. It is a polling snapshot, not a lossless stream: high-volume logs can contain more new data between polls than the window retains. Rotated files are read again at the selected path. Failed reads pause Follow and mark any displayed snapshot stale; closing stops further polling and waits for a currently running bounded read to finish. Disconnects are reported. Regular-file type is checked before each read; symlinks, special devices and NUL-containing binary output are refused. These are best-effort path checks rather than a server-side file lock.

Linux `tail` and the connected account's normal read permission are required. The command uses the trusted existing SSH connection, quotes the file path literally, and applies timeout/output limits. File contents may contain sensitive operational data; copy only the lines needed for your investigation. Nothing is automatically exported or sent to an AI service.

`preview-oncall.png` shows the compact file workspace and nested groups; `preview-log-viewer.png` shows the log window. Both use fictional example data. The test run passed 144 checks, with one procfs integration test skipped in the sandbox; real server reconnects, log rotation and native application builds still require validation in your environment.


## Connected server time

The connection-status row shown above the file browser now displays the connected server's local date, time, timezone abbreviation and UTC offset. Example: **Server time: 23-OCT-2026 12:30:00 CDT (UTC-05:00)**. Each session tab has its own server clock; this is not the workstation's local time.

Nothing is displayed while disconnected, connecting or disconnecting. The clock first appears only after an authenticated connection and a successful server-time query. It clears immediately on disconnect/reconnect or a failed time query; failed/stale replies from an older connection cannot populate a new session's clock. A failed query does not break the SSH connection or replace its status message with a workstation clock.

The app reads `date '+%s|%z|%Z'` over a bounded, read-only SSH command channel on connection and once every 60 seconds. Displayed seconds advance locally using a monotonic clock between synchronizations, keeping SSH traffic to roughly one small query per connected session per minute. Display is an estimate between queries and is not a time-synchronization/NTP diagnostic. Server clock corrections or timezone/DST changes are picked up by the next successful sync. Linux date-format support and permission to execute the command are required. Nothing changes the server's time, timezone, applications or services.

`preview-server-time.png` uses fictional data to show the connected state. The complete suite passed 144 checks, with one environment-dependent procfs test skipped. Live server clock validation and native builds still need to be performed in your environment.
