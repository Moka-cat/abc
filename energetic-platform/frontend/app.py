from __future__ import annotations

import os

import requests
import streamlit as st


API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")

st.set_page_config(page_title="Energetic Platform", layout="wide")
st.title("Energetic Platform")

st.subheader("New ORCA Optimization")

molecule_name = st.text_input("Molecule name", value="h2")
charge = st.number_input("Charge", value=0, step=1)
multiplicity = st.number_input("Multiplicity", value=1, min_value=1, step=1)
xyz = st.text_area(
    "XYZ coordinates without atom count/header",
    value="H 0.000000 0.000000 0.000000\nH 0.000000 0.000000 0.740000",
    height=160,
)

if st.button("Create ORCA job package"):
    response = requests.post(
        f"{API_BASE_URL}/workflows/orca/optimization",
        json={
            "molecule_name": molecule_name,
            "xyz": xyz,
            "charge": charge,
            "multiplicity": multiplicity,
        },
        timeout=60,
    )
    if response.ok:
        st.success("Job package created")
        st.json(response.json())
    else:
        st.error(response.text)

st.subheader("Slurm Status")
job_id = st.text_input("Slurm job id")
if st.button("Query status") and job_id:
    response = requests.get(f"{API_BASE_URL}/workflows/slurm/{job_id}", timeout=30)
    if response.ok:
        st.json(response.json())
    else:
        st.error(response.text)
