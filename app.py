"""ASGI entry point retained at the repository root for simple deployment."""

from nse_dashboard.api.app import create_app

app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=app.state.settings.debug)
