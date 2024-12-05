import os
import sys
import json
import requests
from github import Github
from git import Repo

def get_contextual_files():
    manual_content = ''
    example_contents = ''

    # Read developer manual
    manual_path = os.path.join(os.getcwd(), 'developer_manual.md')
    if os.path.exists(manual_path):
        with open(manual_path, 'r') as f:
            manual_content = f.read()

    # Read example files
    examples_path = os.path.join(os.getcwd(), 'examples')
    if os.path.exists(examples_path):
        for root, dirs, files in os.walk(examples_path):
            for file in files:
                with open(os.path.join(root, file), 'r') as f:
                    content = f.read()
                    example_contents += f"\n### Example File: {file}\n{content}"

    return manual_content, example_contents

def get_changed_files(repo_path, base_branch, head_branch):
    repo = Repo(repo_path)
    repo.git.fetch()
    base_commit = repo.merge_base(base_branch, head_branch)
    diff = repo.git.diff(f'{base_commit[0]}..{head_branch}', '--unified=0')
    return diff

def review_code_with_llm(filename, diff_content, manual_content, example_contents, api_key):
    prompt = f"""
You are a code reviewer. Below is the developer manual and examples:

Developer Manual:
{manual_content}

Examples:
{example_contents}

Now, review the following code diff and provide feedback:

File: {filename}
Diff:
{diff_content}

Provide your feedback in the following JSON format:
{{
  "filename": "{filename}",
  "comments": [
    {{
      "line": integer,  # Line number in the new code
      "comment": "string"
    }},
    ...
  ]
}}
"""

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}',
    }
    payload = {
        "model": "gpt-3.5-turbo",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }

    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers=headers,
        json=payload
    )

    return response.json()

def get_position_in_diff(diff, target_line):
    position = 0
    current_line = None
    for line in diff.split('\n'):
        if line.startswith('@@'):
            # Extract the starting line number
            import re
            match = re.search(r'\+(\d+)(?:,(\d+))?', line)
            if match:
                current_line = int(match.group(1)) - 1
        elif line.startswith('+'):
            current_line += 1
            position += 1
            if current_line == target_line:
                return position
        elif line.startswith('-'):
            continue
        else:
            current_line += 1
    return None

def post_comments(comments, diffs, repo_full_name, pr_number, commit_id, github_token):
    g = Github(github_token)
    repo = g.get_repo(repo_full_name)
    pull_request = repo.get_pull(pr_number)

    for comment in comments:
        filename = comment['filename']
        line = comment['line']
        body = comment['comment']

        # Get the diff for the specific file
        file_diff = diffs.get(filename)
        if not file_diff:
            print(f"No diff found for {filename}")
            continue

        position = get_position_in_diff(file_diff, line)
        if position is None:
            print(f"Could not find position for file {filename} line {line}")
            continue

        try:
            pull_request.create_review_comment(
                body=body,
                commit_id=commit_id,
                path=filename,
                position=position
            )
            print(f"Comment posted on {filename} line {line}")
        except Exception as e:
            print(f"Error posting comment on {filename} line {line}: {e}")

def main():
    try:
        # Get inputs
        openai_api_key = sys.argv[1]
        github_token = os.environ.get('GITHUB_TOKEN')

        # Initialize GitHub client
        g = Github(github_token)
        repo_full_name = os.environ['GITHUB_REPOSITORY']
        repo = g.get_repo(repo_full_name)

        # Get PR information
        event_path = os.environ['GITHUB_EVENT_PATH']
        with open(event_path, 'r') as f:
            event = json.load(f)

        pr_number = event['number']
        pr = repo.get_pull(pr_number)
        base_branch = pr.base.ref
        head_branch = pr.head.ref
        commit_id = pr.head.sha

        # Clone the repo
        repo_path = '/tmp/repo'
        if not os.path.exists(repo_path):
            os.makedirs(repo_path)
        Repo.clone_from(pr.head.repo.clone_url, repo_path, branch=head_branch)
        os.chdir(repo_path)

        # Get the diff of changed files
        diff = get_changed_files(repo_path, base_branch, head_branch)

        # Separate diffs by file
        diffs_by_file = {}
        current_file = None
        current_diff = []
        for line in diff.split('\n'):
            if line.startswith('diff --git'):
                if current_file and current_diff:
                    diffs_by_file[current_file] = '\n'.join(current_diff)
                    current_diff = []
                # Extract filename
                parts = line.split(' ')
                if len(parts) >= 3:
                    file_path = parts[2][2:]  # Remove "b/" prefix
                    current_file = file_path
            elif line.startswith('---') or line.startswith('+++'):
                continue
            elif current_file:
                current_diff.append(line)
        if current_file and current_diff:
            diffs_by_file[current_file] = '\n'.join(current_diff)

        # Get contextual files
        manual_content, example_contents = get_contextual_files()

        # Prepare code snippets and review each file
        all_comments = []
        for filename, file_diff in diffs_by_file.items():
            print(f"Reviewing {filename}...")
            llm_response = review_code_with_llm(
                filename=filename,
                diff_content=file_diff,
                manual_content=manual_content,
                example_contents=example_contents,
                api_key=openai_api_key
            )

            # Parse LLM response
            try:
                llm_text = llm_response['choices'][0]['message']['content']
                # Sometimes the model might output text before or after the JSON. Extract JSON.
                json_start = llm_text.find('{')
                json_end = llm_text.rfind('}') + 1
                llm_json_str = llm_text[json_start:json_end]
                feedback = json.loads(llm_json_str)
                comments = feedback.get('comments', [])
                for comment in comments:
                    comment['filename'] = filename
                all_comments.extend(comments)
            except Exception as e:
                print(f"Error parsing LLM response for {filename}: {e}")
                continue

        # Post comments
        post_comments(
            comments=all_comments,
            diffs=diffs_by_file,
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            commit_id=commit_id,
            github_token=github_token
        )

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
