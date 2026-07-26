from .nodes import FormFrameJobLoader, FormFrameResultSaver

NODE_CLASS_MAPPINGS = {
    "FormFrameJobLoader": FormFrameJobLoader,
    "FormFrameResultSaver": FormFrameResultSaver,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "FormFrameJobLoader": "FormFrame Job Loader",
    "FormFrameResultSaver": "FormFrame Result Saver",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]

