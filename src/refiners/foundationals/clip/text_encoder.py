from torch import Tensor, arange, device as Device, dtype as DType
import refiners.fluxion.layers as fl
from refiners.foundationals.clip.tokenizer import CLIPTokenizer


class TokenEncoder(fl.Embedding):
    structural_attrs = ["vocabulary_size", "embedding_dim"]

    def __init__(
        self,
        vocabulary_size: int,
        embedding_dim: int,
        device: Device | str | None = None,
        dtype: DType | None = None,
    ) -> None:
        self.vocabulary_size = vocabulary_size
        self.embedding_dim = embedding_dim
        super().__init__(
            num_embeddings=vocabulary_size,
            embedding_dim=embedding_dim,
            device=device,
            dtype=dtype,
        )


class PositionalEncoder(fl.Chain):
    structural_attrs = ["max_sequence_length", "embedding_dim"]

    def __init__(
        self,
        max_sequence_length: int,
        embedding_dim: int,
        device: Device | str | None = None,
        dtype: DType | None = None,
    ) -> None:
        self.max_sequence_length = max_sequence_length
        self.embedding_dim = embedding_dim
        super().__init__(
            fl.Lambda(func=self.get_position_ids),
            fl.Embedding(
                num_embeddings=max_sequence_length,
                embedding_dim=embedding_dim,
                device=device,
                dtype=dtype,
            ),
        )

    @property
    def position_ids(self) -> Tensor:
        return arange(end=self.max_sequence_length, device=self.device).reshape(1, -1)

    def get_position_ids(self, x: Tensor) -> Tensor:
        return self.position_ids[:, : x.shape[1]]


class FeedForward(fl.Chain):
    structural_attrs = ["embedding_dim", "feedforward_dim"]

    def __init__(
        self,
        embedding_dim: int,
        feedforward_dim: int,
        device: Device | str | None = None,
        dtype: DType | None = None,
    ) -> None:
        self.embedding_dim = embedding_dim
        self.feedforward_dim = feedforward_dim
        super().__init__(
            fl.Linear(in_features=embedding_dim, out_features=feedforward_dim, device=device, dtype=dtype),
            fl.GeLU(),
            fl.Linear(in_features=feedforward_dim, out_features=embedding_dim, device=device, dtype=dtype),
        )


class TransformerLayer(fl.Chain):
    structural_attrs = ["embedding_dim", "num_attention_heads", "feedforward_dim", "layer_norm_eps"]

    def __init__(
        self,
        embedding_dim: int,
        feedforward_dim: int,
        num_attention_heads: int = 1,
        layer_norm_eps: float = 1e-5,
        device: Device | str | None = None,
        dtype: DType | None = None,
    ) -> None:
        self.embedding_dim = embedding_dim
        self.num_attention_heads = num_attention_heads
        self.feedforward_dim = feedforward_dim
        self.layer_norm_eps = layer_norm_eps
        super().__init__(
            fl.Residual(
                fl.LayerNorm(
                    normalized_shape=embedding_dim,
                    eps=layer_norm_eps,
                    device=device,
                    dtype=dtype,
                ),
                fl.SelfAttention(
                    embedding_dim=embedding_dim,
                    num_heads=num_attention_heads,
                    is_causal=True,
                    device=device,
                    dtype=dtype,
                ),
            ),
            fl.Residual(
                fl.LayerNorm(
                    normalized_shape=embedding_dim,
                    eps=layer_norm_eps,
                    device=device,
                    dtype=dtype,
                ),
                FeedForward(
                    embedding_dim=embedding_dim,
                    feedforward_dim=feedforward_dim,
                    device=device,
                    dtype=dtype,
                ),
            ),
        )


class CLIPTextEncoder(fl.Chain):
    structural_attrs = [
        "embedding_dim",
        "max_sequence_length",
        "vocabulary_size",
        "num_layers",
        "num_attention_heads",
        "feedforward_dim",
        "layer_norm_eps",
        "use_quick_gelu",
    ]

    def __init__(
        self,
        embedding_dim: int = 768,
        max_sequence_length: int = 77,
        vocabulary_size: int = 49408,
        num_layers: int = 12,
        num_attention_heads: int = 12,
        feedforward_dim: int = 3072,
        layer_norm_eps: float = 1e-5,
        use_quick_gelu: bool = False,
        tokenizer: CLIPTokenizer | None = None,
        device: Device | str | None = None,
        dtype: DType | None = None,
    ) -> None:
        self.embedding_dim = embedding_dim
        self.max_sequence_length = max_sequence_length
        self.vocabulary_size = vocabulary_size
        self.num_layers = num_layers
        self.num_attention_heads = num_attention_heads
        self.feedforward_dim = feedforward_dim
        self.layer_norm_eps = layer_norm_eps
        self.use_quick_gelu = use_quick_gelu
        super().__init__(
            tokenizer or CLIPTokenizer(sequence_length=max_sequence_length),
            fl.Converter(set_dtype=False),
            fl.Sum(
                TokenEncoder(
                    vocabulary_size=vocabulary_size,
                    embedding_dim=embedding_dim,
                    device=device,
                    dtype=dtype,
                ),
                PositionalEncoder(
                    max_sequence_length=max_sequence_length,
                    embedding_dim=embedding_dim,
                    device=device,
                    dtype=dtype,
                ),
            ),
            *(
                TransformerLayer(
                    embedding_dim=embedding_dim,
                    num_attention_heads=num_attention_heads,
                    feedforward_dim=feedforward_dim,
                    layer_norm_eps=layer_norm_eps,
                    device=device,
                    dtype=dtype,
                )
                for _ in range(num_layers)
            ),
            fl.LayerNorm(normalized_shape=embedding_dim, eps=layer_norm_eps, device=device, dtype=dtype),
        )
        if use_quick_gelu:
            for gelu, parent in self.walk(predicate=lambda m, _: isinstance(m, fl.GeLU)):
                parent.replace(old_module=gelu, new_module=fl.ApproximateGeLU())

    @property
    def unconditional_text_embedding(self) -> Tensor:
        return self("")


class CLIPTextEncoderL(CLIPTextEncoder):
    """
    CLIPTextEncoderL is the CLIP text encoder with the following parameters:
    embedding_dim=768
    num_layers=12
    num_attention_heads=12
    feedforward_dim=3072
    use_quick_gelu=True

    We replace the GeLU activation function with an approximate GeLU to comply with the original CLIP implementation
    of OpenAI (https://github.com/openai/CLIP/blob/main/clip/model.py#L166)
    """

    def __init__(self, device: Device | str | None = None, dtype: DType | None = None) -> None:
        super().__init__(
            embedding_dim=768,
            num_layers=12,
            num_attention_heads=12,
            feedforward_dim=3072,
            use_quick_gelu=True,
            device=device,
            dtype=dtype,
        )


class CLIPTextEncoderH(CLIPTextEncoder):
    """
    CLIPTextEncoderH is the CLIP text encoder with the following parameters:
    embedding_dim=1024
    num_layers=23
    num_attention_heads=16
    feedforward_dim=4096
    """

    def __init__(self, device: Device | str | None = None, dtype: DType | None = None) -> None:
        super().__init__(
            embedding_dim=1024,
            num_layers=23,
            num_attention_heads=16,
            feedforward_dim=4096,
            device=device,
            dtype=dtype,
        )


class CLIPTextEncoderG(CLIPTextEncoder):
    """
    CLIPTextEncoderG is the CLIP text encoder with the following parameters:
    embedding_dim=1280
    num_layers=32
    num_attention_heads=16
    feedforward_dim=5120
    """

    def __init__(self, device: Device | str | None = None, dtype: DType | None = None) -> None:
        tokenizer = CLIPTokenizer(pad_token_id=0)
        super().__init__(
            embedding_dim=1280,
            num_layers=32,
            num_attention_heads=20,
            feedforward_dim=5120,
            tokenizer=tokenizer,
            device=device,
            dtype=dtype,
        )
