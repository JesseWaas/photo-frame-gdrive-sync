# Photo Frame Config UI (Apps Script)

Edits Drive `config/<hostname>.json` files used by Photo Frame Sync. Focused on visibility rules (`filter` + `schedule`) and crop rules (`filter` + `crop`), with an optional album-folder helper.

## Setup

1. In Google Drive, open your photo sync root and ensure a `config/` folder exists (copy from `config.example/` in this repo).
2. Note folder IDs from the URL (`.../folders/<ID>`):
   - `CONFIG_FOLDER_ID` → the `config/` folder
   - `ALBUMS_FOLDER_ID` → optional `albums/` folder for the folder browser
3. Go to [script.google.com](https://script.google.com) → New project.
4. Replace `Code.gs` with this repo’s `apps-script/Code.gs`.
5. **Add the HTML file (required):**
   - File → New → HTML file
   - When prompted for the name, enter exactly: `index`  
     (the editor will show it as `index.html`)
   - Paste in the contents of this repo’s `apps-script/index.html`
   - Do **not** name the file `Index`, `Index.html`, or `index.html` in the name dialog — only `index`
6. Project Settings → Script properties:
   - `CONFIG_FOLDER_ID` = config folder ID
   - `ALBUMS_FOLDER_ID` = albums folder ID (optional)
7. Deploy → New deployment → Web app  
   - Execute as: Me  
   - Who has access: Only myself (or your domain)
8. **Authorize Drive access once from the editor** (needed before the web app can read folders):
   - Select function `listAlbumChildren` → Run
   - Approve the Google permissions prompt (Drive)
9. Deploy → New deployment → Web app again if you changed code after the last deploy  
   (or Edit deployment → New version).
10. Open the web app URL, load a hostname (must match the Pi hostname), edit rules, Save.

### Folder browser

With `ALBUMS_FOLDER_ID` set, browse like a file manager: breadcrumbs, **Up**, open subfolders, and only the current folder’s contents are listed.

- Browse albums once, then use each section’s buttons to add a visibility or crop rule for the current folder or selection
- Double-click a folder to open it; double-click a file to add a visibility rule

`Code.gs` loads the UI with `HtmlService.createHtmlOutputFromFile('index')`. Apps Script omits the `.html` suffix in that API call; the project file still appears as `index.html`.

## Troubleshooting: `getFolderById` / DriveApp errors

Usually one of these:

1. **Wrong property value** — `CONFIG_FOLDER_ID` must be the ID of the `config/` folder, e.g. from  
   `https://drive.google.com/drive/folders/1AbC...xyz` use only `1AbC...xyz`.  
   Pasting the full URL now works too (the script extracts the ID).
2. **Not authorized** — Run `listHosts` from the Apps Script editor once and accept Drive permission.
3. **No access** — The Google account that owns the deployment must be able to open that folder in Drive.
4. **Stale deployment** — After changing code or properties, create a new deployment version.
