"""Independent console worker. stdout is the JSON event protocol, never the app log."""
import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from app_worker import main
    raise SystemExit(main())
