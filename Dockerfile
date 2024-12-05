FROM python:3.9-slim

# Install git (needed for git commands)
RUN apt-get update && apt-get install -y git

# Install necessary Python packages
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy the action script
COPY code_review.py /app/code_review.py

# Set the working directory
WORKDIR /app

# Run the script
ENTRYPOINT ["python", "code_review.py"]