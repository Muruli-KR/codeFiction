# Django-semihack-starter
Starter template for semi-hackathon

---

## 🚀 Django Semi-Hackathon: Advanced Result Analytics Suite

---

## 📋 Project Details

- **Theme:** Advanced Result Analytics Suite  
- **Team Members:** @rmgowda-17, @Muruli-KR, @paridesai12, @mohithsgowdamohithsgowda-rgb  

---

## ✅ Submission Checklist

- [ ] Code runs with `pip install -r requirements.txt`  
- [ ] `DEBUG=False` in production settings  
- [ ] Working AJAX endpoint (tested live)  
- [ ] CSV/PDF export functional  
- [ ] CO-SDG mapping table completed below  
- [ ] 150-word SDG justification included  

---

## 🎯 CO-SDG Mapping Table

| Course Outcome | How This Project Demonstrates It | SDG Target Addressed |
|---------------|--------------------------------|----------------------|
| CO1: MVT Architecture | Built using Django MVT pattern with models, views, and templates | SDG 4.5 |
| CO2: Models & Forms | Implemented models and forms for structured student data handling | SDG 9.5 |
| CO3: Data Processing | Uses Pandas for processing and analyzing student results | SDG 10.2 |
| CO4: Backend Logic | Performs analytics like averages, toppers, and pass/fail | SDG 4.4 |
| CO5: Dynamic UI | Dashboard with charts and interactive analytics | SDG 9.4 |

---

## 🌍 SDG Justification

This project aligns with **SDG 4: Quality Education (Target 4.5)** by enabling data-driven analysis of student performance through a structured analytics system. It helps educators identify trends, strengths, and learning gaps, improving academic outcomes.  
It also supports **SDG 10: Reduced Inequalities (Target 10.2)** by ensuring fair and unbiased evaluation across different student categories such as gender and quota. The system promotes inclusive decision-making by providing equal visibility into academic performance.  

---

## 📦 Setup Instructions

```bash
git clone https://github.com/cse-sjbit-23cse644-batch2/django-semihack-code-fiction.git
cd django-semihack-code-fiction
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver