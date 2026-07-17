use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

fn rgb888_to_q565(width: u16, height: u16, rgb888_raw: &[u8]) -> Vec<u8> {
    if rgb888_raw.len() % 3 != 0 {
        return Vec::new();
    }
    let mut v = Vec::with_capacity(1024 * 1024);
    let n = (width as usize) * (height as usize);
    let mut rgb565: Vec<u16> = Vec::with_capacity(n);
    for x in (0..rgb888_raw.len()).step_by(3) {
        let r = (rgb888_raw[x] as u32 * 249 + 1014) >> 11;
        let g = (rgb888_raw[x + 1] as u32 * 253 + 505) >> 10;
        let b = (rgb888_raw[x + 2] as u32 * 249 + 1014) >> 11;
        rgb565.push(((r as u16) << 11) | ((g as u16) << 5) | (b as u16));
    }
    q565::encode::Q565EncodeContext::encode_to_vec(width, height, &rgb565, &mut v);
    v
}

/// Porter-Duff "src over dst" matching Pillow Image.alpha_composite (integer path).
fn blend_over_inplace(dst: &mut [u8], src: &[u8]) {
    debug_assert_eq!(dst.len(), src.len());
    for i in (0..dst.len()).step_by(4) {
        let sa = src[i + 3] as u32;
        if sa == 0 {
            continue;
        }
        if sa == 255 {
            dst[i] = src[i];
            dst[i + 1] = src[i + 1];
            dst[i + 2] = src[i + 2];
            dst[i + 3] = 255;
            continue;
        }
        let da = dst[i + 3] as u32;
        let inv_sa = 255 - sa;
        let out_a = sa + (da * inv_sa + 127) / 255;
        if out_a == 0 {
            dst[i] = 0;
            dst[i + 1] = 0;
            dst[i + 2] = 0;
            dst[i + 3] = 0;
            continue;
        }
        let sr = src[i] as u32;
        let sg = src[i + 1] as u32;
        let sb = src[i + 2] as u32;
        let dr = dst[i] as u32;
        let dg = dst[i + 1] as u32;
        let db = dst[i + 2] as u32;
        // premultiplied-ish combine then un-premultiply
        let or = (sr * sa + dr * da * inv_sa / 255 + out_a / 2) / out_a;
        let og = (sg * sa + dg * da * inv_sa / 255 + out_a / 2) / out_a;
        let ob = (sb * sa + db * da * inv_sa / 255 + out_a / 2) / out_a;
        dst[i] = or.min(255) as u8;
        dst[i + 1] = og.min(255) as u8;
        dst[i + 2] = ob.min(255) as u8;
        dst[i + 3] = out_a.min(255) as u8;
    }
}

fn rotate_rgba_ortho(src: &[u8], w: usize, h: usize, ccw: u16) -> (Vec<u8>, usize, usize) {
    match ccw % 360 {
        0 => (src.to_vec(), w, h),
        90 => {
            // (x,y) -> (y, w-1-x); new size h x w
            let mut out = vec![0u8; src.len()];
            let nw = h;
            let nh = w;
            for y in 0..h {
                for x in 0..w {
                    let si = (y * w + x) * 4;
                    let dx = y;
                    let dy = w - 1 - x;
                    let di = (dy * nw + dx) * 4;
                    out[di..di + 4].copy_from_slice(&src[si..si + 4]);
                }
            }
            (out, nw, nh)
        }
        180 => {
            let mut out = vec![0u8; src.len()];
            for y in 0..h {
                for x in 0..w {
                    let si = (y * w + x) * 4;
                    let dx = w - 1 - x;
                    let dy = h - 1 - y;
                    let di = (dy * w + dx) * 4;
                    out[di..di + 4].copy_from_slice(&src[si..si + 4]);
                }
            }
            (out, w, h)
        }
        270 => {
            // (x,y) -> (h-1-y, x); new size h x w
            let mut out = vec![0u8; src.len()];
            let nw = h;
            let nh = w;
            for y in 0..h {
                for x in 0..w {
                    let si = (y * w + x) * 4;
                    let dx = h - 1 - y;
                    let dy = x;
                    let di = (dy * nw + dx) * 4;
                    out[di..di + 4].copy_from_slice(&src[si..si + 4]);
                }
            }
            (out, nw, nh)
        }
        _ => (src.to_vec(), w, h),
    }
}

/// Inscribed ellipse mask like PIL ellipse([(0,0),(w,h)]) — outside → opaque black.
fn apply_circle_mask_to_rgb(rgba: &[u8], w: usize, h: usize) -> Vec<u8> {
    let mut rgb = vec![0u8; w * h * 3];
    let cx = (w as f32 - 1.0) * 0.5;
    let cy = (h as f32 - 1.0) * 0.5;
    // Match PIL ellipse fill: radius to edge midpoints
    let rx = w as f32 * 0.5;
    let ry = h as f32 * 0.5;
    let rx2 = rx * rx;
    let ry2 = ry * ry;
    for y in 0..h {
        for x in 0..w {
            let si = (y * w + x) * 4;
            let di = (y * w + x) * 3;
            let dx = x as f32 - cx;
            let dy = y as f32 - cy;
            let inside = if rx2 > 0.0 && ry2 > 0.0 {
                (dx * dx) / rx2 + (dy * dy) / ry2 <= 1.0
            } else {
                false
            };
            if inside {
                rgb[di] = rgba[si];
                rgb[di + 1] = rgba[si + 1];
                rgb[di + 2] = rgba[si + 2];
            }
            // else leave black
        }
    }
    rgb
}

fn compose_encode(
    width: u16,
    height: u16,
    canvas_rgba: &[u8],
    overlay_rgba: &[u8],
    rotate_ccw: u16,
) -> Result<Vec<u8>, String> {
    let w = width as usize;
    let h = height as usize;
    let need = w * h * 4;
    if canvas_rgba.len() != need {
        return Err(format!(
            "canvas_rgba len {} != {}",
            canvas_rgba.len(),
            need
        ));
    }
    let ccw = rotate_ccw % 360;
    if ccw % 90 != 0 {
        return Err(format!("rotate_ccw {} not orthogonal", rotate_ccw));
    }

    let mut buf = canvas_rgba.to_vec();
    if !overlay_rgba.is_empty() {
        if overlay_rgba.len() != need {
            return Err(format!(
                "overlay_rgba len {} != {}",
                overlay_rgba.len(),
                need
            ));
        }
        blend_over_inplace(&mut buf, overlay_rgba);
    }

    let (rotated, rw, rh) = rotate_rgba_ortho(&buf, w, h, ccw);
    let rgb = apply_circle_mask_to_rgb(&rotated, rw, rh);
    Ok(rgb888_to_q565(rw as u16, rh as u16, &rgb))
}

#[pyfunction]
fn py_encode(py: Python, width: u16, height: u16, rgb888_raw: &[u8]) -> PyObject {
    let v = py.allow_threads(|| rgb888_to_q565(width, height, rgb888_raw));
    PyBytes::new(py, &v).into()
}

/// Blend overlay over canvas, rotate CCW by 0/90/180/270, circle-mask, Q565-encode.
/// `overlay_rgba` may be empty (length 0) to skip blending.
/// `rotate_ccw` must be a multiple of 90.
#[pyfunction]
fn py_compose_encode(
    py: Python,
    width: u16,
    height: u16,
    canvas_rgba: &[u8],
    overlay_rgba: &[u8],
    rotate_ccw: u16,
) -> PyResult<PyObject> {
    let result = py.allow_threads(|| {
        compose_encode(width, height, canvas_rgba, overlay_rgba, rotate_ccw)
    });
    match result {
        Ok(v) => Ok(PyBytes::new(py, &v).into()),
        Err(e) => Err(PyValueError::new_err(e)),
    }
}

#[pymodule]
fn q565_rust(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(py_encode, m)?)?;
    m.add_function(wrap_pyfunction!(py_compose_encode, m)?)?;
    Ok(())
}
