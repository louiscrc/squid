export function Name() {
  return 'Kraken LCD Bridge';
}
export function Version() {
  return '0.3.0-squid';
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
  return [6, 6];
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

const parameters = {
  fps: {
    property: 'fps',
    group: '',
    label: 'FPS',
    type: 'combobox',
    values: ['MAXIMUM', 'SIGNALRGB LIMITED', '20', '10', '5', '1', '0.1'],
    default: 'SIGNALRGB LIMITED',
  },
  screenSize: {
    property: 'screenSize',
    group: '',
    label: 'ScreenSize',
    step: '1',
    type: 'number',
    min: '1',
    max: '80',
    default: '40',
  },
  composition: {
    property: 'composition',
    group: '',
    label: 'Composition mode',
    type: 'combobox',
    values: ['OFF', 'OVERLAY', 'MIX'],
    default: 'OVERLAY',
  },
  lcdOrientation: {
    property: 'lcdOrientation',
    group: '',
    label: 'LCD Orientation',
    type: 'combobox',
    values: ['0', '90', '180', '270'],
    default: '90',
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
  overlayTransparency: {
    property: 'overlayTransparency',
    group: '',
    label: 'Overlay Transparency',
    step: 1,
    type: 'number',
    min: 0,
    max: 100,
    default: 0,
  },
  textOverlay: {
    property: 'textOverlay',
    group: '',
    label: 'Use overlay layout',
    type: 'boolean',
    default: true,
  },
};
export function ControllableParameters() {
  return [
    parameters.fps,
    parameters.screenSize,
    parameters.composition,
    parameters.lcdOrientation,
    parameters.lcdOrientationDegrees,
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

/** True when the canvas slice is effectively black (SignalRGB pause / blank). */
function isEffectivelyDark(bytes) {
  if (!bytes || bytes.length < 12) {
    return false;
  }
  let sum = 0;
  let n = 0;
  const step = Math.max(4, Math.floor(bytes.length / 400));
  for (let i = 0; i + 2 < bytes.length; i += step) {
    sum += bytes[i] + bytes[i + 1] + bytes[i + 2];
    n++;
  }
  // Average channel under ~2/255 → treat as blanked canvas.
  return n > 0 && sum / n < 6;
}

/**
 * Canvas Play/Pause forces device.color() to #000000 (SignalRGB docs).
 * getImageBuffer() does NOT — it keeps streaming a dimmed live frame.
 */
function isCanvasColorBlack() {
  const s = screenSize;
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
  const fine = device.getProperty('lcdOrientationDegrees')?.value;
  if (fine !== undefined && fine !== null && fine !== '') {
    return Number(fine) % 360;
  }
  const preset = device.getProperty('lcdOrientation')?.value;
  return Number(preset ?? 90) % 360;
}

export function onfpsChanged() {
  nextCall = 0;
}

export function onlcdOrientationChanged() {
  postOrientation(device.getProperty('lcdOrientation')?.value ?? 90);
}

export function onlcdOrientationDegreesChanged() {
  postOrientation(device.getProperty('lcdOrientationDegrees')?.value ?? 90);
}

export function onscreenSizeChanged() {
  device.setSize([screenSize + 1, screenSize + 1]);
}

export function onBrightnessChanged() {
  const brightness = device.getBrightness();
  // SignalRGB pause / master dim often drives brightness to 0 while Render still runs.
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
  if (device.getProperty('composition').value === 'OFF') {
    device.removeProperty('overlayTransparency');
    device.removeProperty('textOverlay');
  } else {
    device.addProperty(parameters.overlayTransparency);
    device.addProperty(parameters.textOverlay);
  }
  // Drop legacy props if still present from older plugin versions
  device.removeProperty('spinner');
  device.removeProperty('imageFormat');
  device.removeProperty('colorPalette');
}

export function ontextOverlayChanged() {
  // Layout is edited at http://127.0.0.1:30003/overlay — no per-field SignalRGB props.
}

export function Initialize() {
  device.setName(controller.name);
  onscreenSizeChanged();
  oncompositionChanged();
  try {
    const image = XmlHttp.downloadImage(device.image);
    device.setImageFromBase64(image);
  } catch (error) {
    device.log('Could not retrieve device image');
  }
  // Resume streaming after pause / restart
  const brightness = device.getBrightness();
  if (brightness <= 0) {
    pauseBridge();
  } else {
    resumeBridge(brightness);
  }
  onlcdOrientationDegreesChanged();
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

  // Pause button: color() goes black, but getImageBuffer stays dimmed/live.
  // (Keep this — without it the LCD never true-blanks on SignalRGB pause.)
  if (isCanvasColorBlack()) {
    pauseBridge();
    return false;
  }

  const RGBData = device.getImageBuffer(0, 0, screenSize, screenSize, {
    flipH: false,
    outputWidth: screenSize,
    outputHeight: screenSize,
    format: 'PNG',
  });

  // Do NOT use isEffectivelyDark(RGBData): fullscreen games often darken the
  // capture buffer and that falsely blanked the Kraken.

  if (bridgePaused) {
    resumeBridge(brightness);
  }

  const data = {
    raw: XmlHttp.Bytes2Base64(RGBData),
    rotation: device.rotation,
    lcdOrientation: getLcdOrientationDegrees(),
    composition: device.getProperty('composition').value,
    overlayTransparency: device.getProperty('overlayTransparency')?.value ?? 0,
    textOverlay: device.getProperty('textOverlay')?.value ?? false,
  };
  const fpsConfig = device.getProperty('fps')?.value;
  if (Number(fpsConfig)) {
    nextCall = Date.now() + 1000 / Number(fpsConfig) - 15;
  }

  const async = fpsConfig === 'MAXIMUM';
  XmlHttp.Post(BRIDGE_ADDRESS + '/frame', () => {}, data, async);
}

export function Shutdown(suspend) {
  pauseBridge();
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

  static Post(url, callback, data, async = true) {
    const xhr = new XMLHttpRequest();
    xhr.timeout = 1000;
    xhr.open('POST', url, async);

    xhr.setRequestHeader('Accept', 'application/json');
    xhr.setRequestHeader('Content-Type', 'application/json');

    xhr.onreadystatechange = callback.bind(null, xhr);
    xhr.send(JSON.stringify(data));
  }
}
