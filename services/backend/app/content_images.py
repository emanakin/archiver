"""Bounded local image validation/rendering; no remote fetches or secret paths."""

import hashlib
import io
import math

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, UnidentifiedImageError

from .content_storage import MAX_BYTES, read_original
from .content_storage import asset_path as asset_path
from .execution_errors import OperationBlocked

MAX_PIXELS = 24_000_000
RESAMPLE = Image.Resampling.LANCZOS


def validate_image(data):
    if not data or len(data) > MAX_BYTES:
        raise OperationBlocked("Upload a JPEG or PNG no larger than 8MiB")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {"JPEG", "PNG"} or getattr(image, "n_frames", 1) != 1:
                raise OperationBlocked("Only single-frame JPEG and PNG images are supported")
            if image.width * image.height > MAX_PIXELS or min(image.size) < 32:
                raise OperationBlocked("Choose an image between32 pixels per side and24 megapixels")
            result = {
                "mime": "image/jpeg" if image.format == "JPEG" else "image/png",
                "width": image.width,
                "height": image.height,
                "byte_size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            image.verify()
            return result
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        raise OperationBlocked("The uploaded image could not be validated") from None


def native_feed_frame(dimensions):
    width, height = dimensions
    output_width = min(1080, max(320, width))
    output_height = round(output_width * height / width)
    return output_width, min(
        output_width * 5 // 4, max(math.ceil(output_width * 47 / 90), output_height)
    )


def carousel_frame(config, cover, values):
    if values.get("fit_mode") != "native":
        return None
    with Image.open(io.BytesIO(read_original(config, cover))) as original:
        width, height = original.size
        if original.getexif().get(274, 1) in {5, 6, 7, 8}:
            width, height = height, width
    if values["rotation"] in {90, 270}:
        width, height = height, width
    return native_feed_frame((width, height))


def extend_sides(picture, width):
    left = (width - picture.width) // 2
    right = width - picture.width - left
    result = Image.new("RGB", (width, picture.height))
    result.paste(picture, (left, 0))
    for padding, x, edge in ((left, 0, 0), (right, left + picture.width, picture.width)):
        if not padding:
            continue
        extent = min(padding, picture.width)
        bounds = (0, extent) if edge == 0 else (edge - extent, edge)
        with picture.crop((bounds[0], 0, bounds[1], picture.height)) as strip:
            with ImageOps.mirror(strip) as reflected:
                with reflected.resize((padding, picture.height), RESAMPLE) as extension:
                    result.paste(extension, (x, 0))
    return result


def fit_with_edge_extension(picture, frame):
    foreground = ImageOps.contain(picture, frame, RESAMPLE)
    if foreground.size == frame:
        return foreground
    with foreground:
        if foreground.width < frame[0]:
            return extend_sides(foreground, frame[0])
        with foreground.transpose(Image.Transpose.ROTATE_90) as turned:
            with extend_sides(turned, frame[1]) as extended:
                return extended.transpose(Image.Transpose.ROTATE_270)


def render_image(config, asset, values, frame=None):
    raw = read_original(config, asset)
    try:
        with Image.open(io.BytesIO(raw)) as original:
            ImageOps.exif_transpose(original, in_place=True)
            picture = original.convert("RGB")
        if values["rotation"]:
            rotated = picture.rotate(values["rotation"], expand=True)
            picture.close()
            picture = rotated
        mode = values["fit_mode"]
        if values["kind"] == "story_photo":
            size = (1080, 1920)
        elif mode == "native":
            size = frame or native_feed_frame(picture.size)
        else:
            size = (1080, 1350)
        if mode == "native":
            result = fit_with_edge_extension(picture, size)
        elif mode == "fill":
            result = ImageOps.fit(picture, size, method=Image.Resampling.LANCZOS)
        elif mode == "blend":
            with ImageOps.fit(picture, size, method=Image.Resampling.LANCZOS) as backdrop:
                result = backdrop.filter(ImageFilter.GaussianBlur(min(size) / 30))
            with ImageOps.contain(picture, size, method=Image.Resampling.LANCZOS) as foreground:
                result.paste(
                    foreground,
                    ((size[0] - foreground.width) // 2, (size[1] - foreground.height) // 2),
                )
        else:
            picture.thumbnail(size, Image.Resampling.LANCZOS)
            result = Image.new("RGB", size, (17, 20, 18))
            result.paste(picture, ((size[0] - picture.width) // 2, (size[1] - picture.height) // 2))
        picture.close()
        if values.get("overlay_text"):
            draw = ImageDraw.Draw(result)
            font = ImageFont.load_default(size=46)
            lines, line = [], ""
            for character in " ".join(values["overlay_text"].split()):
                if line and draw.textlength(line + character, font=font) > size[0] - 160:
                    lines.append(line.rstrip())
                    line = character.lstrip()
                else:
                    line += character
            text = "\n".join([*lines, line])
            box = draw.multiline_textbbox((0, 0), text, font=font, spacing=10, align="center")
            height, width = box[3] - box[1], box[2] - box[0]
            top = {"top": 130, "center": (size[1] - height) // 2, "bottom": size[1] - height - 150}[
                values["text_position"]
            ]
            left = (size[0] - width) // 2
            draw.rounded_rectangle(
                (left - 28, top - 24, left + width + 28, top + height + 38),
                radius=20,
                fill=(17, 20, 18),
            )
            draw.multiline_text(
                (left, top), text, fill="white", font=font, spacing=10, align="center"
            )
        output = io.BytesIO()
        result.save(output, "JPEG", quality=90)
        result.close()
        return output.getvalue()
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        raise OperationBlocked("The draft image could not be rendered") from None
