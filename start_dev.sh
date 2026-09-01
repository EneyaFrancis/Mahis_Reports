#!/bin/bash
set -e
# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Dev mode runs app.py directly via Flask's own dev server (app.run(...) in
# app.py, threaded=True) -- there is no gunicorn involved here at all; that's
# start_prod.sh's job. `nohup ... &` actually backgrounds this so the shell
# stays usable and the process survives the terminal closing -- `nohup`
# alone (no trailing &) does not background a process, it only prevents it
# from being killed by SIGHUP once something else has backgrounded it.
echo "Starting development server (Flask dev server via app.py, not gunicorn)..."
nohup python3 app.py > dev_server.log 2>&1 &

echo "Started in development mode (PID $!), logging to dev_server.log"