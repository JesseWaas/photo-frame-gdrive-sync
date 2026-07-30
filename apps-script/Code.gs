/**
 * Photo Frame Sync — host config editor.
 *
 * Script properties (Project Settings → Script properties):
 *   CONFIG_FOLDER_ID  — Drive folder ID of config/ (required)
 *   ALBUMS_FOLDER_ID  — Drive folder ID of albums/ (optional; enables folder helper)
 *
 * Requires an HTML file in this project named exactly "index"
 * (shown as index.html in the Apps Script editor).
 */

var DEFAULTS_NAME = 'defaults.json';
var HOST_SUFFIX = '.json';

function doGet() {
  return HtmlService.createHtmlOutputFromFile('index')
    .setTitle('Photo Frame Config')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

/**
 * Accept a bare folder ID or a Drive folder URL and return the ID.
 * Example URL: https://drive.google.com/drive/folders/ABCD123...
 */
function normalizeFolderId_(value, propertyName) {
  var raw = String(value || '').trim();
  if (!raw) {
    throw new Error(
      'Missing script property ' + propertyName + '. ' +
      'Open the config/ folder in Drive and copy the ID from the URL after /folders/'
    );
  }

  var match = raw.match(/\/folders\/([a-zA-Z0-9_-]+)/);
  if (match) {
    return match[1];
  }

  // Strip query/hash if someone pasted a partial URL fragment.
  raw = raw.split('?')[0].split('#')[0].trim();
  if (!/^[a-zA-Z0-9_-]+$/.test(raw)) {
    throw new Error(
      'Invalid ' + propertyName + '. Use only the folder ID ' +
      '(the part after /folders/ in the Drive URL), not the full page title.'
    );
  }
  return raw;
}

function getFolderByProperty_(propertyName) {
  var props = PropertiesService.getScriptProperties();
  var id = normalizeFolderId_(props.getProperty(propertyName), propertyName);
  try {
    return DriveApp.getFolderById(id);
  } catch (err) {
    throw new Error(
      'Could not open Drive folder for ' + propertyName + ' (' + id + '). ' +
      'Check: (1) the ID is the config/ folder ID, (2) your Google account can open that folder, ' +
      '(3) authorize the script — in the Apps Script editor, select listHosts and click Run once. ' +
      'Original error: ' + err
    );
  }
}

function getConfigFolder_() {
  return getFolderByProperty_('CONFIG_FOLDER_ID');
}

function readJsonFile_(folder, name) {
  var files = folder.getFilesByName(name);
  if (!files.hasNext()) {
    return {};
  }
  var text = files.next().getBlob().getDataAsString('UTF-8');
  var parsed = JSON.parse(text);
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error(name + ' must be a JSON object');
  }
  return parsed;
}

function writeJsonFile_(folder, name, data) {
  var body = JSON.stringify(data, null, 2) + '\n';
  var files = folder.getFilesByName(name);
  if (files.hasNext()) {
    files.next().setContent(body);
    while (files.hasNext()) {
      files.next().setTrashed(true);
    }
    return;
  }
  folder.createFile(name, body, MimeType.PLAIN_TEXT);
}

function listHosts() {
  var folder = getConfigFolder_();
  var hosts = [];
  var files = folder.getFiles();
  while (files.hasNext()) {
    var file = files.next();
    var name = file.getName();
    if (name === DEFAULTS_NAME || name.slice(-HOST_SUFFIX.length) !== HOST_SUFFIX) {
      continue;
    }
    hosts.push(name.slice(0, -HOST_SUFFIX.length));
  }
  hosts.sort();
  return {
    hosts: hosts,
    defaults: readJsonFile_(folder, DEFAULTS_NAME),
    albumsConfigured: !!PropertiesService.getScriptProperties().getProperty('ALBUMS_FOLDER_ID'),
  };
}

function loadHost(hostname) {
  hostname = String(hostname || '').trim();
  if (!hostname) {
    throw new Error('hostname is required');
  }
  var folder = getConfigFolder_();
  var defaults = readJsonFile_(folder, DEFAULTS_NAME);
  var host = readJsonFile_(folder, hostname + HOST_SUFFIX);
  return {
    hostname: hostname,
    host: host,
    defaults: defaults,
    merged: Object.assign({}, defaults, host),
  };
}

function saveHost(hostname, hostConfig) {
  hostname = String(hostname || '').trim();
  if (!/^[A-Za-z0-9._-]+$/.test(hostname)) {
    throw new Error('hostname must be alphanumeric (plus . _ -)');
  }
  if (!hostConfig || typeof hostConfig !== 'object' || Array.isArray(hostConfig)) {
    throw new Error('hostConfig must be an object');
  }

  var visibilityRules = hostConfig.visibility_rules;
  if (visibilityRules != null) {
    if (!Array.isArray(visibilityRules)) {
      throw new Error('visibility_rules must be an array');
    }
    hostConfig.visibility_rules = visibilityRules.map(function (rule, index) {
      if (!rule || typeof rule !== 'object') {
        throw new Error('visibility_rule ' + index + ' must be an object');
      }
      var filter = String(rule.filter || '').trim();
      if (!filter) {
        throw new Error('visibility_rule ' + index + ' needs a filter');
      }
      return {
        filter: filter,
        schedule: String(rule.schedule || '* * * * *').trim(),
      };
    });
  }

  var cropRules = hostConfig.crop_rules;
  if (cropRules != null) {
    if (!Array.isArray(cropRules)) {
      throw new Error('crop_rules must be an array');
    }
    hostConfig.crop_rules = cropRules.map(function (rule, index) {
      if (!rule || typeof rule !== 'object') {
        throw new Error('crop_rule ' + index + ' must be an object');
      }
      var filter = String(rule.filter || '').trim();
      if (!filter) {
        throw new Error('crop_rule ' + index + ' needs a filter');
      }
      return {
        filter: filter,
        crop: String(rule.crop || 'none').trim().toLowerCase(),
      };
    });
  }

  // Keep only known keys so the form cannot accumulate junk.
  var cleaned = {};
  ['aspect_ratio', 'date', 'max_bytes', 'max_files', 'rotation', 'preserve_unmanaged', 'visibility_rules', 'crop_rules'].forEach(function (key) {
    if (hostConfig[key] !== undefined && hostConfig[key] !== null && hostConfig[key] !== '') {
      cleaned[key] = hostConfig[key];
    }
  });

  writeJsonFile_(getConfigFolder_(), hostname + HOST_SUFFIX, cleaned);
  return loadHost(hostname);
}

/**
 * List only the immediate children of a folder under ALBUMS_FOLDER_ID.
 * relativePath is '' for albums root, or e.g. 'family/2024'.
 */
function listAlbumChildren(relativePath) {
  var raw = PropertiesService.getScriptProperties().getProperty('ALBUMS_FOLDER_ID');
  if (!raw || !String(raw).trim()) {
    return {
      albumsConfigured: false,
      relativePath: '',
      currentPath: 'albums',
      breadcrumbs: [{ name: 'albums', path: '' }],
      items: [],
    };
  }

  var parts = normalizeRelativePath_(relativePath);
  var folder = resolveAlbumFolder_(parts);
  var items = [];

  var folders = folder.getFolders();
  while (folders.hasNext()) {
    var sub = folders.next();
    var subParts = parts.concat([sub.getName()]);
    var folderPath = albumPath_(subParts);
    items.push({
      name: sub.getName(),
      path: folderPath + '/',
      relativePath: subParts.join('/'),
      type: 'folder',
      filter: '^' + escapeRegex_(folderPath) + '/.*',
    });
  }

  var files = folder.getFiles();
  while (files.hasNext()) {
    var file = files.next();
    var fileParts = parts.concat([file.getName()]);
    var filePath = albumPath_(fileParts);
    items.push({
      name: file.getName(),
      path: filePath,
      relativePath: fileParts.join('/'),
      type: 'file',
      filter: '^' + escapeRegex_(filePath) + '$',
    });
  }

  items.sort(function (a, b) {
    if (a.type !== b.type) {
      return a.type === 'folder' ? -1 : 1;
    }
    return a.name.localeCompare(b.name);
  });

  var breadcrumbs = [{ name: 'albums', path: '' }];
  for (var i = 0; i < parts.length; i++) {
    breadcrumbs.push({
      name: parts[i],
      path: parts.slice(0, i + 1).join('/'),
    });
  }

  var currentPath = albumPath_(parts);
  var currentFilter = parts.length
    ? '^' + escapeRegex_(currentPath) + '/.*'
    : '^albums/.*';

  return {
    albumsConfigured: true,
    relativePath: parts.join('/'),
    currentPath: currentPath + (parts.length ? '/' : ''),
    currentFilter: currentFilter,
    breadcrumbs: breadcrumbs,
    items: items,
  };
}

function normalizeRelativePath_(relativePath) {
  return String(relativePath || '')
    .replace(/^\/+|\/+$/g, '')
    .split('/')
    .filter(function (part) {
      return part && part !== '.' && part !== '..';
    });
}

function resolveAlbumFolder_(parts) {
  var folder = getFolderByProperty_('ALBUMS_FOLDER_ID');
  for (var i = 0; i < parts.length; i++) {
    var matches = folder.getFoldersByName(parts[i]);
    if (!matches.hasNext()) {
      throw new Error('Folder not found under albums/: ' + parts.slice(0, i + 1).join('/'));
    }
    folder = matches.next();
  }
  return folder;
}

function albumPath_(parts) {
  return ['albums'].concat(parts).join('/');
}

function escapeRegex_(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
