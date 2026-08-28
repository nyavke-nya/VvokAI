import os

import cv2
import numpy as np
import torch
import onnxruntime as ort
from utils import load_toml_as_dict
import warnings

warnings.filterwarnings(
    "ignore",
    message=".*'pin_memory' argument is set as true but no accelerator is found.*",
    category=UserWarning
)


def _numpy_nms(boxes, scores, iou_threshold=0.6):
    if len(boxes) == 0:
        return np.array([], dtype=np.int32)

    x1 = boxes[:, 0]
    y1 = boxes[:, 1]
    x2 = boxes[:, 2]
    y2 = boxes[:, 3]

    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]

    keep = []

    while order.size > 0:
        i = order[0]
        keep.append(i)

        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])

        w = np.maximum(0.0, xx2 - xx1)
        h = np.maximum(0.0, yy2 - yy1)

        inter = w * h
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-6)

        inds = np.where(iou <= iou_threshold)[0]
        order = order[inds + 1]

    return np.array(keep, dtype=np.int32)


def _normalize_yolo_output(raw_output):
    """
    Accepts either:
        outputs
        outputs[0]

    Supports common YOLO ONNX shapes:
        (1, 84, 8400)
        (1, 8400, 84)
        (84, 8400)
        (8400, 84)

    Returns:
        prediction with shape (num_boxes, num_channels)
    """

    if isinstance(raw_output, (list, tuple)):
        prediction = raw_output[0]
    else:
        prediction = raw_output

    prediction = np.asarray(prediction)

    if prediction.ndim == 3:
        prediction = prediction[0]

    if prediction.ndim != 2:
        raise ValueError(f"Unexpected YOLO output shape: {prediction.shape}")

    # YOLOv8 ONNX often gives (84, 8400), needs transpose to (8400, 84)
    if prediction.shape[0] < prediction.shape[1] and prediction.shape[0] <= 256:
        prediction = prediction.T

    return prediction


def _postprocess_raw(raw_output, conf_tresh=0.6, iou_thresh=0.6):
    prediction = _normalize_yolo_output(raw_output)

    n_detections = prediction.shape[0]
    n_classes = prediction.shape[1] - 4

    if n_classes <= 0:
        return []

    boxes_cxcywh = prediction[:, :4]
    class_scores = prediction[:, 4:]

    class_ids = np.argmax(class_scores, axis=1)
    confidences = class_scores[np.arange(n_detections), class_ids]

    mask = confidences >= conf_tresh

    if not np.any(mask):
        return []

    boxes_cxcywh = boxes_cxcywh[mask]
    confidences = confidences[mask]
    class_ids = class_ids[mask]

    x1 = boxes_cxcywh[:, 0] - boxes_cxcywh[:, 2] / 2
    y1 = boxes_cxcywh[:, 1] - boxes_cxcywh[:, 3] / 2
    x2 = boxes_cxcywh[:, 0] + boxes_cxcywh[:, 2] / 2
    y2 = boxes_cxcywh[:, 1] + boxes_cxcywh[:, 3] / 2

    boxes_xyxy = np.stack([x1, y1, x2, y2], axis=1)

    results = []

    for cls in np.unique(class_ids):
        cls_mask = class_ids == cls

        cls_boxes = boxes_xyxy[cls_mask]
        cls_scores = confidences[cls_mask]

        keep = _numpy_nms(cls_boxes, cls_scores, iou_thresh)

        if len(keep) == 0:
            continue

        kept_boxes = cls_boxes[keep]
        kept_scores = cls_scores[keep]
        kept_cls = np.full((len(keep), 1), cls, dtype=np.float32)

        det = np.hstack(
            [
                kept_boxes,
                kept_scores.reshape(-1, 1),
                kept_cls,
            ]
        ).astype(np.float32, copy=False)

        results.append(det)

    return results


class Detect:
    def __init__(self, model_path, ignore_classes=None, classes=None, input_size=(640, 640)):
        threads_to_use = load_toml_as_dict("cfg/general_config.toml")['used_threads']

        def get_optimal_threads(max_limit=6):
            threads = os.cpu_count()
            threads_amount = min(max(2, threads // 2), max_limit)
            print(f"Detected {threads} CPU threads, using {threads_amount} threads.")
            return threads_amount

        self.optimal_threads_amount = get_optimal_threads() if threads_to_use == "auto" else int(threads_to_use)
        cv2.setNumThreads(self.optimal_threads_amount)
        torch.set_num_threads(self.optimal_threads_amount)
        self.preferred_device = load_toml_as_dict("cfg/general_config.toml")['cpu_or_gpu']
        self.model_path = model_path
        self.classes = classes
        self.ignore_classes = set(ignore_classes) if ignore_classes else set()
        self.input_size = input_size
        self.model, self.device = self.load_model()
        self.input_name = self.model.get_inputs()[0].name
        self._padded_img_buffer = np.full(
            (1, 3, self.input_size[0], self.input_size[1]),
            128.0 / 255.0,
            dtype=np.float32
        )

    @staticmethod
    def preload_cuda_libraries():
        """Make onnxruntime find the CUDA/cuDNN DLLs shipped as pip packages.

        Modern onnxruntime-gpu does not bundle the CUDA runtime; it comes from
        the nvidia-* wheels, which install into site-packages/nvidia rather
        than anywhere on PATH. Without this call onnxruntime fails to load
        onnxruntime_providers_cuda.dll, reports the failure only at warning
        level, and silently falls back to CPU - which looks exactly like "CUDA
        is not supported" while the GPU sits idle. Measured on an RTX 3080 Ti:
        5.6 ms per inference on CUDA against 20.8 ms on CPU.
        """
        preload = getattr(ort, "preload_dlls", None)
        if preload is None:
            return  # onnxruntime < 1.21 resolves its own dependencies
        try:
            preload()
        except Exception as exc:
            print(f"Could not preload CUDA libraries ({exc}); GPU may be unavailable.")

    def provider_order(self):
        """Which execution providers to try, best first.

        A list rather than a single choice, because whether a provider works
        cannot be known until a session is actually built on it - see
        load_model.
        """
        available = ort.get_available_providers()
        if self.preferred_device not in ("gpu", "auto"):
            return ["CPUExecutionProvider"]

        order = [name for name in ("CUDAExecutionProvider", "DmlExecutionProvider",
                                   "AzureExecutionProvider")
                 if name in available]
        order.append("CPUExecutionProvider")
        return order

    def load_model(self):
        self.preload_cuda_libraries()

        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        so.intra_op_num_threads = self.optimal_threads_amount
        so.inter_op_num_threads = self.optimal_threads_amount

        # Try each provider in turn instead of committing to one.
        #
        # get_available_providers() reports what onnxruntime was BUILT with,
        # not what will run. There are two ways that bites. A provider whose
        # DLLs are missing is dropped silently at session creation, so the
        # requested provider and the running one differ - that is what the
        # warning below is for. But a provider whose DLLs are all present and
        # whose GPU is simply too old to have kernels in the build RAISES:
        #
        #   CUDA failure 8: the function requires an architectural feature
        #   absent from the device; GPU=0 ... cublasCreate(&cublas_handle_)
        #
        # which used to take the whole bot down at startup with a wall of
        # onnxruntime internals. A card too old for CUDA is a reason to use
        # DirectML or the CPU, not a reason to refuse to run.
        order = self.provider_order()
        problems = []
        model = None
        for index, onnx_provider in enumerate(order):
            try:
                model = ort.InferenceSession(self.model_path, sess_options=so,
                                             providers=[onnx_provider])
                break
            except Exception as exc:
                problems.append((onnx_provider, exc))
                remaining = order[index + 1:]
                print(f"{self.provider_label(onnx_provider)} could not start "
                      f"({self.short_reason(exc)})."
                      + (f" Trying {self.provider_label(remaining[0])}."
                         if remaining else ""))

        if model is None:
            reasons = "; ".join(f"{name}: {self.short_reason(exc)}"
                                for name, exc in problems)
            raise RuntimeError(f"No execution provider could load "
                               f"{self.model_path} ({reasons})")

        active_provider = model.get_providers()[0] if model.get_providers() else onnx_provider
        if active_provider != onnx_provider:
            print(
                f"WARNING: requested {onnx_provider} but onnxruntime fell back to "
                f"{active_provider}. The GPU is NOT being used."
            )
            onnx_provider = active_provider

        print(f"Using {self.provider_label(onnx_provider)}")
        return model, onnx_provider

    @staticmethod
    def provider_label(provider):
        return {
            "CUDAExecutionProvider": "CUDA GPU",
            "DmlExecutionProvider": "DirectML GPU",
            "AzureExecutionProvider": "Azure",
            "CPUExecutionProvider": "CPU",
        }.get(provider, provider)

    @staticmethod
    def short_reason(exc):
        """One readable line out of an onnxruntime exception.

        Their messages are several lines of build paths and source locations
        around one sentence that says what is wrong. Keep that sentence.
        """
        text = " ".join(str(exc).split())
        for marker in ("CUDA failure", "Failed to load", "LoadLibrary failed",
                       "requires", "is missing"):
            position = text.find(marker)
            if position >= 0:
                return text[position:position + 160]
        return text[:160]

    def preprocess_image(self, img):
        h, w = img.shape[:2]

        scale = min(self.input_size[0] / h, self.input_size[1] / w)
        new_w = int(w * scale)
        new_h = int(h * scale)

        resized_img = cv2.resize(
            img,
            (new_w, new_h),
            interpolation=cv2.INTER_LINEAR
        )

        img_float = resized_img.astype(np.float32, copy=True)
        np.multiply(img_float, 1.0 / 255.0, out=img_float)

        self._padded_img_buffer[0, 0, :new_h, :new_w] = img_float[:, :, 0]
        self._padded_img_buffer[0, 1, :new_h, :new_w] = img_float[:, :, 1]
        self._padded_img_buffer[0, 2, :new_h, :new_w] = img_float[:, :, 2]

        return self._padded_img_buffer, new_w, new_h

    def postprocess(self, raw_output, orig_img_shape, resized_shape, conf_tresh=0.6):
        detections = _postprocess_raw(
            raw_output,
            conf_tresh=conf_tresh,
            iou_thresh=0.6
        )

        orig_h, orig_w = orig_img_shape
        resized_w, resized_h = resized_shape

        scale_w = orig_w / resized_w
        scale_h = orig_h / resized_h

        results = []

        for det in detections:
            if len(det):
                det[:, 0] *= scale_w
                det[:, 1] *= scale_h
                det[:, 2] *= scale_w
                det[:, 3] *= scale_h
                results.append(det)

        return results

    def detect_objects(self, img, conf_tresh=0.6):
        orig_h, orig_w = img.shape[:2]

        preprocessed_img, resized_w, resized_h = self.preprocess_image(img)

        outputs = self.model.run(
            None,
            {self.input_name: preprocessed_img}
        )

        detections = self.postprocess(
            outputs,
            (orig_h, orig_w),
            (resized_w, resized_h),
            conf_tresh
        )

        results = {}

        for detection in detections:
            for row in detection:
                x1, y1, x2, y2 = int(row[0]), int(row[1]), int(row[2]), int(row[3])
                class_id = int(row[5])

                if self.classes is None:
                    class_name = str(class_id)
                else:
                    if class_id < 0 or class_id >= len(self.classes):
                        print(
                            f"WARNING: class_id {class_id} is out of range "
                            f"(classes length: {len(self.classes)}). Detection ignored."
                        )
                        continue

                    class_name = self.classes[class_id]

                if class_id in self.ignore_classes or class_name in self.ignore_classes:
                    continue

                if class_name not in results:
                    results[class_name] = []

                results[class_name].append([x1, y1, x2, y2])

        return results
