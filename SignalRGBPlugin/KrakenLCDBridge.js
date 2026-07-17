export function Name() {
  return 'Kraken LCD Bridge';
}
export function Version() {
  return '0.5.3-squid';
}
export function Type() {
  return 'network';
}
export function Publisher() {
  return 'Brokenmass';
}
export function Documentation() {
  return 'N/A';
}
export function Size() {
  return [41, 41];
}
export function DefaultPosition() {
  return [165, 60];
}
export function DefaultScale() {
  return 1.0;
}
export function DefaultComponentBrand() {
  return 'CompGen';
}
export function LedNames() {
  return [];
}
export function LedPositions() {
  return [];
}

/** Fixed canvas sample size (was ScreenSize slider). */
const SCREEN_SIZE = 40;

/**
 * FPS dropdown. MAX = as fast as SignalRGB calls Render (async posts).
 */
const parameters = {
  fps: {
    property: 'fps',
    group: '',
    label: 'FPS',
    type: 'combobox',
    values: ['0.1', '0.5', '1', '2', '3', '4', '5', '10', '20', '24', '30', 'MAX'],
    default: '1',
  },
  lcdOrientationDegrees: {
    property: 'lcdOrientationDegrees',
    group: '',
    label: 'LCD Orientation Degrees',
    step: 1,
    type: 'number',
    min: 0,
    max: 359,
    default: 90,
  },
  composition: {
    property: 'composition',
    group: '',
    label: 'Composition mode',
    type: 'combobox',
    values: ['OFF', 'OVERLAY', 'MONITOR', 'GIF'],
    default: 'MONITOR',
  },
  layoutEditorUrl: {
    property: 'layoutEditorUrl',
    group: '',
    label: 'Layout Editor URL',
    type: 'combobox',
    values: ['http://127.0.0.1:30003/monitor'],
    default: 'http://127.0.0.1:30003/monitor',
  },
  gifEditorUrl: {
    property: 'gifEditorUrl',
    group: '',
    label: 'Gif Editor URL',
    type: 'combobox',
    values: ['http://127.0.0.1:30003/gif'],
    default: 'http://127.0.0.1:30003/gif',
  },
  shutdownColor: {
    property: 'shutdownColor',
    group: '',
    label: 'Shutdown color',
    type: 'color',
    default: '#000000',
  },
  // OVERLAY (classic) props — added dynamically
  spinner: {
    property: 'spinner',
    group: '',
    label: 'Spinner',
    type: 'combobox',
    values: ['OFF', 'STATIC', 'CPU', 'PUMP'],
    default: 'STATIC',
  },
  overlayMetric: {
    property: 'overlayMetric',
    group: '',
    label: 'Overlay data',
    type: 'combobox',
    values: ['Liquid', 'Pump', 'CPU %', 'CPU °'],
    default: 'Liquid',
  },
  overlayBgMode: {
    property: 'overlayBgMode',
    group: '',
    label: 'Background',
    type: 'combobox',
    values: ['Transparent', 'Fixed'],
    default: 'Transparent',
  },
  overlayBgColor: {
    property: 'overlayBgColor',
    group: '',
    label: 'Background color',
    type: 'color',
    default: '#000000',
  },
  titleText: {
    property: 'titleText',
    group: '',
    label: 'Title',
    type: 'textfield',
    default: 'SignalRGB',
  },
  titleFontSize: {
    property: 'titleFontSize',
    group: '',
    label: 'Title size',
    step: 1,
    type: 'number',
    min: 10,
    max: 200,
    default: 40,
  },
  sensorFontSize: {
    property: 'sensorFontSize',
    group: '',
    label: 'Value size',
    step: 1,
    type: 'number',
    min: 10,
    max: 320,
    default: 160,
  },
  sensorLabelFontSize: {
    property: 'sensorLabelFontSize',
    group: '',
    label: 'Label size',
    step: 1,
    type: 'number',
    min: 10,
    max: 200,
    default: 40,
  },
};

export function ControllableParameters() {
  return [
    parameters.fps,
    parameters.lcdOrientationDegrees,
    parameters.composition,
    parameters.shutdownColor,
  ];
}

/* global
controller:readonly
discovery: readonly
*/

const BRIDGE_ADDRESS = 'http://127.0.0.1:30003';
let nextCall = 0;
let bridgePaused = false;

function postOrientation(degrees) {
  XmlHttp.Post(
    BRIDGE_ADDRESS + '/orientation',
    () => {},
    {degrees: Number(degrees) % 360},
    true
  );
}

function pauseBridge() {
  if (bridgePaused) {
    return;
  }
  bridgePaused = true;
  XmlHttp.Post(BRIDGE_ADDRESS + '/pause', () => {}, {}, false);
}

function resumeBridge(brightness) {
  bridgePaused = false;
  XmlHttp.Post(
    BRIDGE_ADDRESS + '/resume',
    () => {},
    {brightness: brightness},
    false
  );
}

/**
 * Canvas Play/Pause forces device.color() to #000000 (SignalRGB docs).
 * getImageBuffer() does NOT — it keeps streaming a dimmed live frame.
 */
function isCanvasColorBlack() {
  const s = SCREEN_SIZE;
  const points = [
    [1, 1],
    [(s / 2) | 0, (s / 2) | 0],
    [s - 2, s - 2],
    [(s / 4) | 0, ((3 * s) / 4) | 0],
    [((3 * s) / 4) | 0, (s / 4) | 0],
  ];
  for (let i = 0; i < points.length; i++) {
    const c = device.color(points[i][0], points[i][1]);
    if (!c || c[0] > 3 || c[1] > 3 || c[2] > 3) {
      return false;
    }
  }
  return true;
}

function getLcdOrientationDegrees() {
  return Number(device.getProperty('lcdOrientationDegrees')?.value ?? 90) % 360;
}

function clearLegacyProps() {
  device.removeProperty('lcdOrientation');
  device.removeProperty('overlayTransparency');
  device.removeProperty('textOverlay');
  device.removeProperty('imageFormat');
  device.removeProperty('colorPalette');
  device.removeProperty('fpsIndex');
  device.removeProperty('screenSize');
  device.removeProperty('gifPath');
  device.removeProperty('openGifEditor');
  device.removeProperty('customEditorUrl');
  device.removeProperty('openCustomEditor');
}

function setOverlayProps(on) {
  const keys = [
    'spinner',
    'overlayMetric',
    'overlayBgMode',
    'overlayBgColor',
    'titleText',
    'titleFontSize',
    'sensorFontSize',
    'sensorLabelFontSize',
  ];
  for (const k of keys) {
    if (on) {
      device.addProperty(parameters[k]);
    } else {
      device.removeProperty(k);
    }
  }
  syncOverlayBgColorProp();
}

function syncOverlayBgColorProp() {
  const mode = device.getProperty('composition')?.value || 'OFF';
  if (mode !== 'OVERLAY') {
    return;
  }
  const bg = device.getProperty('overlayBgMode')?.value || 'Transparent';
  if (bg === 'Fixed') {
    device.addProperty(parameters.overlayBgColor);
  } else {
    device.removeProperty('overlayBgColor');
  }
}

function setMonitorProps(on) {
  if (on) {
    device.addProperty(parameters.layoutEditorUrl);
  } else {
    device.removeProperty('layoutEditorUrl');
    device.removeProperty('customEditorUrl');
    device.removeProperty('openCustomEditor');
  }
}

function setGifProps(on) {
  if (on) {
    device.addProperty(parameters.gifEditorUrl);
  } else {
    device.removeProperty('gifEditorUrl');
    device.removeProperty('openGifEditor');
    device.removeProperty('gifPath');
  }
}

function pushRestoreGif() {
  XmlHttp.Post(
    BRIDGE_ADDRESS + '/gif',
    () => {},
    {
      restore: true,
      degrees: getLcdOrientationDegrees(),
    },
    true,
    120000
  );
}

export function onfpsChanged() {
  nextCall = 0;
}

export function onlcdOrientationDegreesChanged() {
  postOrientation(device.getProperty('lcdOrientationDegrees')?.value ?? 90);
}

export function onBrightnessChanged() {
  const brightness = device.getBrightness();
  if (brightness <= 0) {
    pauseBridge();
    return;
  }
  if (bridgePaused) {
    resumeBridge(brightness);
    return;
  }
  XmlHttp.Post(
    BRIDGE_ADDRESS + '/brightness',
    () => {},
    {brightness: brightness},
    false
  );
}

export function oncompositionChanged() {
  clearLegacyProps();
  const mode = device.getProperty('composition')?.value || 'OFF';
  // EDITOR/CUSTOM kept as aliases for older saved configs
  const isMonitor =
    mode === 'MONITOR' || mode === 'EDITOR' || mode === 'CUSTOM';
  setOverlayProps(mode === 'OVERLAY');
  setMonitorProps(isMonitor);
  setGifProps(mode === 'GIF');
  if (mode === 'GIF') {
    pushRestoreGif();
  } else {
    const brightness = device.getBrightness();
    if (brightness > 0) {
      resumeBridge(brightness);
    }
  }
}

export function onoverlayBgModeChanged() {
  syncOverlayBgColorProp();
}

export function Initialize() {
  device.setName(controller.name);
  device.setSize([SCREEN_SIZE + 1, SCREEN_SIZE + 1]);
  clearLegacyProps();
  oncompositionChanged();
  try {
    const image = XmlHttp.downloadImage(device.image);
    device.setImageFromBase64(image);
  } catch (error) {
    device.log('Could not retrieve device image');
  }
  const brightness = device.getBrightness();
  if (brightness <= 0) {
    pauseBridge();
  } else if (device.getProperty('composition')?.value !== 'GIF') {
    resumeBridge(brightness);
  }
  onlcdOrientationDegreesChanged();
  onfpsChanged();
}

export function Render() {
  if (!controller.online || Date.now() < nextCall) {
    return false;
  }

  const brightness = device.getBrightness();
  if (brightness <= 0) {
    pauseBridge();
    return false;
  }

  const mode = device.getProperty('composition')?.value || 'OFF';

  // GIF mode: firmware loops the uploaded bucket; keep SignalRGB device online
  // but do not stream canvas frames. Load / replace GIFs via the web editor.
  if (mode === 'GIF') {
    nextCall = Date.now() + 1000;
    return false;
  }

  if (isCanvasColorBlack()) {
    pauseBridge();
    return false;
  }

  const RGBData = device.getImageBuffer(0, 0, SCREEN_SIZE, SCREEN_SIZE, {
    flipH: false,
    outputWidth: SCREEN_SIZE,
    outputHeight: SCREEN_SIZE,
    format: 'PNG',
  });

  if (bridgePaused) {
    resumeBridge(brightness);
  }

  const data = {
    raw: XmlHttp.Bytes2Base64(RGBData),
    rotation: device.rotation,
    lcdOrientation: getLcdOrientationDegrees(),
    composition: mode,
    spinner: device.getProperty('spinner')?.value ?? 'OFF',
    overlayMetric: device.getProperty('overlayMetric')?.value ?? 'Liquid',
    overlayBgMode: device.getProperty('overlayBgMode')?.value ?? 'Transparent',
    overlayBgColor: device.getProperty('overlayBgColor')?.value ?? '#000000',
    titleText: device.getProperty('titleText')?.value ?? 'SignalRGB',
    titleFontSize: device.getProperty('titleFontSize')?.value ?? 40,
    sensorFontSize: device.getProperty('sensorFontSize')?.value ?? 160,
    sensorLabelFontSize: device.getProperty('sensorLabelFontSize')?.value ?? 40,
  };

  const fpsConfig = device.getProperty('fps')?.value;
  if (Number(fpsConfig)) {
    nextCall = Date.now() + 1000 / Number(fpsConfig) - 15;
  }

  const async = fpsConfig === 'MAX';
  XmlHttp.Post(BRIDGE_ADDRESS + '/frame', () => {}, data, async);
}

export function Shutdown(suspend) {
  const color = device.getProperty('shutdownColor')?.value ?? '#000000';
  XmlHttp.Post(
    BRIDGE_ADDRESS + '/shutdown',
    () => {},
    {color: color},
    false
  );
  bridgePaused = true;
}

export function DiscoveryService() {
  this.IconUrl = `${BRIDGE_ADDRESS}/images/plugin.png`;
  this.Initialize = function () {
    service.log('Initializing Plugin!');
    this.lastUpdate = 0;
  };

  this.ReadInfo = function (xhr) {
    if (xhr.readyState === 4) {
      if (xhr.status === 200 && xhr.responseText) {
        this.deviceInfo = JSON.parse(xhr.responseText);
        if (!this.controller) {
          this.controller = new KrakenLCDBridgeController(this.deviceInfo);
          service.addController(this.controller);
        }
        this.controller.updateStatus({online: true});
      } else if (this.controller) {
        this.controller.updateStatus({online: false});
      }
    }
  };

  this.Update = function () {
    const currentTime = Date.now();
    const self = this;
    if (currentTime - this.lastUpdate >= 2000) {
      this.lastUpdate = currentTime;
      XmlHttp.Get(
        BRIDGE_ADDRESS,
        function (xhr) {
          self.ReadInfo(xhr);
        },
        true
      );
    }
  };

  this.Discovered = function () {};
}

class KrakenLCDBridgeController {
  constructor(info) {
    this.id = info.serial;
    this.name = info.name;
    this.resolution = info.resolution;
    this.renderingMode = info.renderingMode;
    this.image = info.image;
    this.online = true;
    this.lastUpdate = Date.now();
    this.announcedController = false;
  }

  updateStatus({online}) {
    this.online = online;
    this.update();
  }

  update() {
    service.updateController(this);
    if (!this.announcedController) {
      this.announcedController = true;
      service.announceController(this);
    }
  }
}

class XmlHttp {
  static Bytes2Base64(bytes) {
    for (let i = 0; i < bytes.length; i++) {
      if (bytes[i] > 255 || bytes[i] < 0) {
        throw new Error('Invalid bytes');
      }
    }

    const base64Chars =
      'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';

    let out = '';

    for (let i = 0; i < bytes.length; i += 3) {
      const groupsOfSix = [undefined, undefined, undefined, undefined];
      groupsOfSix[0] = bytes[i] >> 2;
      groupsOfSix[1] = (bytes[i] & 0x03) << 4;
      if (bytes.length > i + 1) {
        groupsOfSix[1] |= bytes[i + 1] >> 4;
        groupsOfSix[2] = (bytes[i + 1] & 0x0f) << 2;
      }
      if (bytes.length > i + 2) {
        groupsOfSix[2] |= bytes[i + 2] >> 6;
        groupsOfSix[3] = bytes[i + 2] & 0x3f;
      }
      for (let j = 0; j < groupsOfSix.length; j++) {
        if (typeof groupsOfSix[j] === 'undefined') {
          out += '=';
        } else {
          out += base64Chars[groupsOfSix[j]];
        }
      }
    }
    return out;
  }
  static downloadImage(url) {
    const xhr = new XMLHttpRequest();
    xhr.open('GET', controller.image, false);
    xhr.responseType = 'arraybuffer';
    xhr.send(null);

    if (xhr.status === 200) {
      return XmlHttp.Bytes2Base64(new Uint8Array(xhr.response));
    } else {
      throw new Error(`Request error ${xhr.status}`);
    }
  }
  static Get(url, callback, async = true) {
    const xhr = new XMLHttpRequest();
    xhr.timeout = 1000;
    xhr.open('GET', url, async);

    xhr.setRequestHeader('Accept', 'application/json');
    xhr.setRequestHeader('Content-Type', 'application/json');

    xhr.onreadystatechange = callback.bind(null, xhr);
    xhr.send();
  }

  static Post(url, callback, data, async = true, timeoutMs = 1000) {
    const xhr = new XMLHttpRequest();
    xhr.timeout = timeoutMs;
    xhr.open('POST', url, async);

    xhr.setRequestHeader('Accept', 'application/json');
    xhr.setRequestHeader('Content-Type', 'application/json');

    xhr.onreadystatechange = callback.bind(null, xhr);
    xhr.send(JSON.stringify(data));
  }
}
