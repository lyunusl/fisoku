"""The custom blocks Fisoku registers in AugeLab Studio.

Studio generates each block file from these typed specifications (node.custom.create);
the processing code below is Fisoku's own.

Fisoku OCR runs RapidOCR (PP-OCRv4 ONNX models) from the "fisoku_engine" folder that
Fisoku places next to Studio's custom block folder. The block finds it relative to its
own file, so a scene never stores a user-specific path. Nothing is pip-installed into
Studio: the engine's extra libraries (onnxruntime, shapely) are appended to the end of
sys.path, so a Studio that already has its own copies keeps using them.
"""
from __future__ import annotations

OP_CODE = "FisokuOCR"
TITLE = "Fisoku OCR (RapidOCR)"
ENGINE_DIR_NAME = "fisoku_engine"  # sibling of Studio's custom_blocks folder

PROCESS_CODE = """\
outputs['Words JSON'] = '[]'
outputs['Status'] = ''
img = inputs['Image']
if img is None:
    outputs['Status'] = 'error: no image'
    return
engine = state.get('engine')
if engine is None:
    here = globals().get('__file__')
    if here:
        folder = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(here))), 'fisoku_engine')
    else:
        folder = os.path.join(os.environ.get('APPDATA', ''), 'AugeLab Studio', 'marketplace', 'fisoku_engine')
    if not os.path.isdir(os.path.join(folder, 'rapidocr_onnxruntime')):
        outputs['Status'] = 'error: OCR engine not found (start Fisoku once to install it)'
        return
    for p in (folder, os.path.join(folder, 'deps')):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.append(p)
    try:
        engine = importlib.import_module('rapidocr_onnxruntime').RapidOCR()
    except Exception as exc:
        outputs['Status'] = 'error: engine load failed: %s: %s' % (type(exc).__name__, exc)
        return
    state['engine'] = engine
try:
    arr = numpy.asarray(img)
    if arr.ndim == 2:
        arr = numpy.stack([arr, arr, arr], axis=-1)
    elif arr.ndim == 3 and arr.shape[2] == 4:
        arr = numpy.ascontiguousarray(arr[:, :, :3])
    result, _ = engine(arr)
except Exception as exc:
    outputs['Status'] = 'error: OCR failed: %s: %s' % (type(exc).__name__, exc)
    return
items = []
for box, text, score in (result or []):
    items.append({'t': str(text), 'b': [[float(p[0]), float(p[1])] for p in box], 's': float(score)})
outputs['Words JSON'] = json.dumps(items, ensure_ascii=False)
outputs['Status'] = 'ok %d' % len(items)
"""

FLATTEN_OP_CODE = "FisokuFlatten"
FLATTEN_TITLE = "Fisoku Light Flatten"

# Divides the image by a heavily blurred copy of itself: glare, shadows and the grey of
# a faded thermal print are flattened, so the remaining contrast is the ink itself.
FLATTEN_CODE = """\
img = inputs['Image']
outputs['Image'] = img
if img is None:
    return
arr = numpy.asarray(img)
if arr.ndim == 3:
    gray = cv2.cvtColor(numpy.ascontiguousarray(arr[:, :, :3]), cv2.COLOR_BGR2GRAY)
else:
    gray = arr
g = gray.astype(numpy.float32)
background = cv2.GaussianBlur(g, (0, 0), max(float(parameters['sigma']), 1.0))
flat = numpy.clip(g / (background + 1.0) * 235.0, 0, 255).astype(numpy.uint8)
outputs['Image'] = cv2.cvtColor(flat, cv2.COLOR_GRAY2BGR)
"""

FLATTEN_SPEC = {
    "resolverKey": "fisoku_flatten",
    "name": FLATTEN_OP_CODE,
    "opCode": FLATTEN_OP_CODE,
    "title": FLATTEN_TITLE,
    "kind": "computation",
    "description": (
        "Evens out lighting on photographed paper (glare, shadow, faded thermal print) by "
        "dividing the image by a Gaussian-blurred copy of itself. Sigma: blur radius in pixels."
    ),
    "imports": ["cv2", "numpy"],
    "inputs": [{"name": "Image", "type": "ImageAny"}],
    "outputs": [{"name": "Image", "type": "ImageAny"}],
    "parameters": [{"key": "sigma", "label": "Sigma", "valueType": "number", "default": 61}],
    "processCode": FLATTEN_CODE,
}

SPEC = {
    "resolverKey": "fisoku_ocr",
    "name": OP_CODE,
    "opCode": OP_CODE,
    "title": TITLE,
    "kind": "computation",
    "description": (
        "Reads printed text with RapidOCR (PP-OCRv4 ONNX models) for receipts. Outputs every "
        "detected text line with its 4-point box and confidence as one JSON string, and a "
        "Status ('ok N' or the error). The engine lives in marketplace/fisoku_engine, placed "
        "there by Fisoku; no packages are installed into Studio."
    ),
    "imports": ["sys", "os", "json", "importlib", "numpy"],
    "inputs": [{"name": "Image", "type": "ImageAny"}],
    "outputs": [{"name": "Words JSON", "type": "String"}, {"name": "Status", "type": "String"}],
    "parameters": [],
    "processCode": PROCESS_CODE,
}

SPECS = [SPEC, FLATTEN_SPEC]
