"""Local dashboard package (DEVF-082, DEVF-083).

The dashboard is an optional component — install with
``pip install '.[dashboard]'`` to pull in FastAPI + uvicorn. The
backend module lazy-imports its FastAPI dependencies so importing
:mod:`devforge` itself never requires the dashboard extras.
"""
