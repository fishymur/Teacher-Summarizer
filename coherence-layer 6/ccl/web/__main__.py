from .server import main

if __name__ == "__main__":
    import os

    main(port=int(os.environ.get("CCL_PORT", "8000")))
