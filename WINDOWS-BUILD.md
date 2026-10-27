# Build SSH Workbench for Windows

This download contains build-ready source, not a precompiled executable. A Windows build host is required. The existing SSH Workbench source and features are unchanged.

## Build using a Windows machine

1. Install 64-bit Python 3.12 from https://www.python.org/downloads/windows/ and include the Python launcher (`py`).
2. Extract this package and open the `ssh-workbench` folder.
3. Double-click **build_windows.cmd**. It creates a dedicated virtual environment, installs the build dependencies and builds the executable. Internet access is required for the first build.
4. After a successful build, open **dist-windows/onefile/SSH Workbench.exe** to validate it.
5. Share **release/SSH-Workbench-Windows-x64-onefile.zip**. The recipient extracts it and runs the executable; Python is not needed on their machine.

From Command Prompt or PowerShell, the equivalent build is:

```text
py -3.12 build_windows.py
```

For a portable folder build, which is easier to diagnose if the single executable has a startup problem:

```text
py -3.12 build_windows.py --mode onedir
```

That produces `dist-windows/onedir/SSH Workbench/SSH Workbench.exe` and its dependencies. Share the entire generated ZIP, not the executable alone.

## Build from a Mac using GitHub Actions

Use a repository approved for this project. No code has been uploaded or workflow started on your behalf.

1. Put the **contents** of the extracted `ssh-workbench` directory at the repository root: `app.py`, `build_windows.py`, `requirements.txt`, the other Python modules and `.github/workflows/build-windows.yml` must be at the matching paths.
2. Include the `.github` directory; Finder hides names beginning with a dot. Command+Shift+Period shows it. Do not commit your local virtual environment, `dist`/`build` output or connection profile files.
3. Commit the workflow to the default branch.
4. Open the repository's **Actions** tab → **Build Windows executable** → **Run workflow**. Choose **onefile** for one executable, or **onedir** for a portable folder.
5. After the run succeeds, download the **SSH-Workbench-Windows-x64** artifact from that run. Extract the artifact and then the release ZIP inside it.
6. Run the executable on a Windows x64 machine and verify a connection and SFTP operations.

The workflow uses a Windows runner and does not require any application credentials, SSH keys or server profiles. It publishes a build artifact for seven days; it does not create a public release or deploy the application. Company-hosted GitHub installations may require equivalent approved action versions/runners.

## Validation and limits

The builder checks imports before packaging, checks the output has a Windows executable header and writes a SHA-256 checksum for the release ZIP. These checks do not replace launching and exercising the packaged application on Windows. This source package has not been compiled or run on Windows in this session.

The executable is unsigned. Profiles are kept locally at `%USERPROFILE%\.ssh-workbench`; saved passwords are not included. Existing workstation profiles are not shipped in the release archive.
