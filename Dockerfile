FROM python:3.9-slim

# Install git (needed for git commands)
RUN apt-get update && apt-get install -y git

# Set the working directory inside the container
WORKDIR /app

# Copy the action script and requirements into the image
COPY code_review.py requirements.txt /app/

# Install necessary Python packages
RUN pip install --no-cache-dir -r requirements.txt

# Set the entrypoint to run your script
ENTRYPOINT ["python", "code_review.py"]