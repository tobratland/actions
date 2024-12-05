FROM python:3.9-slim

# Install git (needed for git commands)
RUN apt-get update && apt-get install -y git

# Copy the action script and requirements into the image
COPY code_review.py requirements.txt /

# Install necessary Python packages
RUN pip install --no-cache-dir -r /requirements.txt

# Set the entrypoint to run your script
ENTRYPOINT ["python", "/code_review.py"]
