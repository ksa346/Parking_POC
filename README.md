**How to setup code in the local system**
Prerequisite before running the code – 
  1.	Install postgres. If not then go for sqlite.
  2.	Install node.js and add it into system variables. 
Go to Edit system variables -> inside advance -> edit path -> add node.js path there e.g., C://program files/nodejs

**For running the backend -**
1.	In anaconda prompt, first create Conda environment – 
  conda create -n parking312 python=3.12 -y
2.	Activate it – 
  conda activate parking312   
3.	Install requirements by going inside backend folder – 
  pip install -r requirements.txt       
4.	Go to backend folder and then run it – 
  uvicorn app.main:app --host 127.0.0.1 --port 8000
	Note – For checking yolo’s health - http://127.0.0.1:8000/api/v1/health


**For running frontend –** 
  1.	Go to blazor-frontend folder and open separate command prompt
  2.	Type dotnet run
Note – For checking UI - http://localhost:5173/
