"""Only the six current contract-3 node IDs are exported; no legacy aliases."""
from .parameter_nodes import VPSeed, VPSlider, VPPrompt, VPBatch
from .media_nodes import VPImage
from .output_nodes import VPSendToPS

NODE_CLASSES = [VPImage, VPSeed, VPSlider, VPPrompt, VPBatch, VPSendToPS]
