import folder_paths
import nodes


WEB_DIRECTORY = "./web"


class MirrorOptionalReferenceImage(nodes.LoadImage):
    @classmethod
    def INPUT_TYPES(cls):
        inputs = super().INPUT_TYPES()
        files, options = inputs["required"]["image"]
        inputs["required"]["image"] = ([""] + files, options)
        inputs["required"]["enabled"] = ("BOOLEAN", {"default": True})
        return inputs

    RETURN_TYPES = ("IMAGE", "MASK", "BOOLEAN")
    RETURN_NAMES = ("image", "mask", "reference_active")
    CATEGORY = "image"

    def load_image(self, image, enabled=True):
        # Mirror templates may retain a filename whose upload no longer exists.
        if not enabled or not image or not folder_paths.exists_annotated_filepath(image):
            return (None, None, False)
        image_tensor, mask = super().load_image(image)
        return (image_tensor, mask, True)

    @classmethod
    def VALIDATE_INPUTS(cls, image, enabled=True):
        return True

    @classmethod
    def IS_CHANGED(cls, image, enabled=True):
        if not enabled or not image or not folder_paths.exists_annotated_filepath(image):
            return "no-reference"
        return super().IS_CHANGED(image)


class MirrorAnyReferenceActive:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "reference_1": ("BOOLEAN", {"default": False}),
            "reference_2": ("BOOLEAN", {"default": False}),
            "reference_3": ("BOOLEAN", {"default": False}),
            "reference_4": ("BOOLEAN", {"default": False}),
        }}

    RETURN_TYPES = ("BOOLEAN",)
    FUNCTION = "any_active"
    CATEGORY = "image"

    def any_active(self, reference_1, reference_2, reference_3, reference_4):
        return (reference_1 or reference_2 or reference_3 or reference_4,)


NODE_CLASS_MAPPINGS = {
    "MirrorOptionalReferenceImage": MirrorOptionalReferenceImage,
    "MirrorAnyReferenceActive": MirrorAnyReferenceActive,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "MirrorOptionalReferenceImage": "参考图（可选）",
    "MirrorAnyReferenceActive": "使用有效参考图",
}
