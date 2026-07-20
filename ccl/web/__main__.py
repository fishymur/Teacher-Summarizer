from .server import main

if __name__ == "__main__":
    # All configuration comes from the environment (PORT/CCL_PORT, HOST, CCL_DB,
    # ANTHROPIC_API_KEY). See server.main().
    main()
