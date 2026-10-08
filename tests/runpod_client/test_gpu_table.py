def test_rtx_4080_super_is_canonical() -> None:
    """Live catalogue row: 16 GB, $0.28 community / $0.50 secure.

    Without it a provider on that card cannot be registered and the dossier
    validator rejects it in recommended_gpu_classes.
    """
    from pitwall.runpod_client.gpu import GPU_VRAM_GB

    assert GPU_VRAM_GB["NVIDIA GeForce RTX 4080 SUPER"] == 16
