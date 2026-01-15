try:
    import cloudscraper
    print("cloudscraper: installed")
except ImportError:
    print("cloudscraper: not installed")

try:
    import curl_cffi
    print("curl_cffi: installed")
except ImportError:
    print("curl_cffi: not installed")
