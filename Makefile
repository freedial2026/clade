.PHONY: validate test lint repo-map changed quality task-status

validate:
	python scripts/validate_template.py

test:
	python -m unittest discover -s tests -p 'test_*.py'

lint:
	python -m compileall -q scripts tests .claude/hooks src

repo-map:
	python scripts/repo_map.py --max-depth 4

changed:
	python scripts/changed_files.py

quality:
	python scripts/quality_gate.py

task-status:
	python scripts/task_status.py
