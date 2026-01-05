# Deployment Guide for Crypto Live Assistant

This application is containerized using Docker, making it compatible with almost all modern cloud platforms (Render, Railway, Fly.io, Heroku, AWS App Runner).

## Option 1: Deploy on Render.com (Recommended - Easiest)
Render offers a hassle-free deployment for Docker apps.

1.  **Push your code to GitHub**: Make sure this project is in a GitHub repository.
2.  **Sign up**: Go to [render.com](https://render.com) and sign up/login.
3.  **New Web Service**:
    *   Click **New +** -> **Web Service**.
    *   Connect your GitHub repository.
4.  **Configure**:
    *   **Name**: `crypto-assistant` (or whatever you like).
    *   **Region**: Choose closest to you.
    *   **Runtime**: Select **Docker**.
    *   **Build Command**: Leave blank (Render detects Dockerfile).
    *   **Start Command**: Leave blank (Render uses Dockerfile CMD).
    *   **Plan**: **Free** is okay for testing, but typically **Starter** ($7/mo) is better for WebSockets performance, as Free tier spins down after inactivity.
5.  **Deploy**: Click **Create Web Service**.

Render will build the Docker container and deploy it. Once done, it will give you a URL like `https://crypto-assistant.onrender.com`.

## Option 2: Deploy on Railway.app
Railway is excellent for persistent connections like WebSockets.

1.  **Push to GitHub**.
2.  **Login**: Go to [railway.app](https://railway.app).
3.  **New Project** -> **Deploy from GitHub repo**.
4.  **Settings**:
    *   Railway usually auto-detects the Dockerfile.
    *   It will automatically expose port `8000` or whatever `PORT` env var is set to.
5.  **Domain**: Railway assigns a default domain.

## Option 3: Run Locally with Docker
If you want to run it on a VPS or locally:

1.  Build:
    ```bash
    docker build -t crypto-assistant .
    ```
2.  Run:
    ```bash
    docker run -p 8000:8000 crypto-assistant
    ```
3.  Access at `http://localhost:8000`.

## Notes
*   **WebSockets**: Ensure your provider supports WebSockets (Render and Railway both do).
*   **Environment Variables**: If you add API keys later, remember to add them in the Dashboard of your cloud provider.
