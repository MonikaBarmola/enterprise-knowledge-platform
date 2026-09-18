#!/bin/bash
python rag.py
python -c "from auth import setup_database; setup_database(create_demo_users=True)"
streamlit run app.py --server.port $PORT --server.address 0.0.0.0