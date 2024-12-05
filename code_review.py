import os
import sys
import json
import requests
from github import Github
from git import Git, Repo


def get_contextual_files(repo_path):
    manual_content = ''
    example_contents = ''

    # Use the GITHUB_WORKSPACE environment variable
    repo_path = os.environ.get('GITHUB_WORKSPACE', '/github/workspace')

    # Read developer manual
    manual_path = os.path.join(repo_path, 'developer_manual.md')
    if os.path.exists(manual_path):
        with open(manual_path, 'r') as f:
            manual_content = f.read()
    else:
        print("No developer_manual.md found.")

    # Read example files
    examples_path = os.path.join(repo_path, 'examples')
    if os.path.exists(examples_path):
        for root, dirs, files in os.walk(examples_path):
            for file in files:
                with open(os.path.join(root, file), 'r') as f:
                    content = f.read()
                    example_contents += f"\n### Example File: {file}\n{content}"
    else:
        print("No examples directory found.")

    return manual_content, example_contents

def get_changed_files(repo_path, base_branch, head_branch, file_extensions):
    repo = Repo(repo_path)
    origin = repo.remotes.origin

    # Fetch all branches
    origin.fetch()

    # Ensure the base branch exists locally
    if base_branch not in repo.heads:
        print(f"Base branch {base_branch} not found locally. Creating it.")
        repo.create_head(base_branch, origin.refs[base_branch])
    else:
        repo.heads[base_branch].set_tracking_branch(origin.refs[base_branch])

    # Checkout the head branch
    repo.git.checkout(head_branch)

    # Get the common ancestor
    base_commit = repo.merge_base(base_branch, head_branch)
    if not base_commit:
        raise Exception(f"Could not find common ancestor between {base_branch} and {head_branch}")

    # Get the diff
    diff_index = base_commit[0].diff(head_branch, create_patch=True)

    # Filter diffs by file extension
    filtered_diffs = []
    for diff in diff_index:
        if diff.a_path:
            file_path = diff.a_path
        else:
            file_path = diff.b_path

        if any(file_path.endswith(ext) for ext in file_extensions):
            filtered_diffs.append(diff)

    return filtered_diffs

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
        openai_api_key = sys.argv[1]
        file_types_input = sys.argv[2] if len(sys.argv) > 2 else ''
        github_token = os.environ.get('GITHUB_TOKEN')

        # Parse file types
        if file_types_input:
            file_extensions = [ext.strip() for ext in file_types_input.split(',')]
        else:
            file_extensions = []

        # Initialize GitHub client
        g = Github(github_token)
        repo_full_name = os.environ['GITHUB_REPOSITORY']
        repo = g.get_repo(repo_full_name)

        # Get PR information
        event_path = os.environ['GITHUB_EVENT_PATH']
        with open(event_path, 'r') as f:
            event = json.load(f)

        pr_number = event['pull_request']['number']
        pr = repo.get_pull(pr_number)
        base_branch = pr.base.ref
        head_branch = pr.head.ref
        commit_id = pr.head.sha

        # Clone the repo
        repo_path = '/github/workspace'  # Already checked out by actions/checkout
        os.chdir(repo_path)

        # Configure Git to consider the directory safe
        git_cmd = Git(repo_path)
        git_cmd.config('--global', '--add', 'safe.directory', repo_path)

        # Initialize Repo object
        repo_git = Repo(repo_path)

        # Proceed with your Git operations
        diffs = get_changed_files(repo_git, base_branch, head_branch, file_extensions)

        # Get contextual files
        manual_content, example_contents = get_contextual_files(repo_path)

        # Prepare code snippets and review each file
        all_comments = []
        diffs_by_file = {}
        for diff in diffs:
            if diff.a_path:
                filename = diff.a_path
            else:
                filename = diff.b_path

            print(f"Reviewing {filename}...")
            diff_content = diff.diff.decode('utf-8', errors='replace')
            diffs_by_file[filename] = diff_content

            llm_response = review_code_with_llm(
                filename=filename,
                diff_content=diff_content,
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
