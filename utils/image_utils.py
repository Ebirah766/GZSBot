def get_display_image_url(url: str) -> str:
    if not url:
        return None

    url = url.strip()

    if "upload.wikimedia.org" in url:
        return url

    if "commons.wikimedia.org/wiki/File:" in url:
        return None

    if "wikipedia.org/wiki/" in url:
        return None

    return url