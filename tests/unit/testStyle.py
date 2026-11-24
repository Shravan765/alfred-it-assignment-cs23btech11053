'''Executable source-style checker and self-tests for the assignment conventions.'''

import ast
import io
import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def checkSource(source: str) -> list[str]:
	'''Return style violations for one Python source string.'''
	issues = []
	for number, line in enumerate(source.splitlines(), 1):
		if line.startswith(' ') and line.strip() and not line.lstrip().startswith('#'):
			issues.append(f'line {number}: indentation must use tabs')
	try:
		tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
	except tokenize.TokenError:
		return issues + ['tokenize: invalid source']
	for token in tokens:
		if token.type == tokenize.INDENT and not token.string.startswith('\t'):
			issues.append(f'line {token.start[0]}: indentation must use tabs')
	comments = {token.start[0] for token in tokens if token.type == tokenize.COMMENT and 'style: external-name' in token.string}
	try:
		tree = ast.parse(source)
	except SyntaxError:
		return issues + ['ast: invalid source']
	parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
	for node in ast.walk(tree):
		if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
			if not (node.name.startswith('__') and node.name.endswith('__')) and not re.fullmatch(r'[a-z][a-zA-Z0-9]*', node.name):
				issues.append(f'line {node.lineno}: function name {node.name}')
			if not ast.get_docstring(node):
				issues.append(f'line {node.lineno}: missing function docstring')
			elif not hasSingleQuoteDocstring(node, tokens):
				issues.append(f'line {node.lineno}: docstring must use triple single quotes')
			arguments = list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
			arguments += [argument for argument in (node.args.vararg, node.args.kwarg) if argument is not None]
			for argument in arguments:
				if not re.fullmatch(r'[a-z_][a-z0-9_]*', argument.arg):
					issues.append(f'line {argument.lineno}: argument name {argument.arg}')
		elif isinstance(node, ast.ClassDef):
			if not re.fullmatch(r'[A-Z][a-zA-Z0-9]*', node.name):
				issues.append(f'line {node.lineno}: class name {node.name}')
			if not ast.get_docstring(node):
				issues.append(f'line {node.lineno}: missing class docstring')
			elif not hasSingleQuoteDocstring(node, tokens):
				issues.append(f'line {node.lineno}: class docstring must use triple single quotes')
		elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
			if node.lineno in comments:
				continue
			if isModuleAssignment(node, parents):
				valid = re.fullmatch(r'[a-z_][a-z0-9_]*|[A-Z][A-Z0-9_]*', node.id)
			else:
				valid = re.fullmatch(r'[a-z_][a-z0-9_]*', node.id)
			if not valid:
				issues.append(f'line {node.lineno}: assigned name {node.id}')
	if not ast.get_docstring(tree):
		issues.append('module: missing docstring')
	elif not hasSingleQuoteDocstring(tree, tokens):
		issues.append('module: docstring must use triple single quotes')
	return issues


def hasSingleQuoteDocstring(node, tokens) -> bool:
	'''Check the source token used by a module, class or function docstring.'''
	statement = node.body[0]
	line = statement.value.lineno if isinstance(statement, ast.Expr) else statement.lineno
	return any(token.type == tokenize.STRING and token.start[0] == line and token.string.startswith("'''") for token in tokens)


def isModuleAssignment(node, parents) -> bool:
	'''Identify assignments outside every function, class and lambda scope.'''
	parent = parents.get(node)
	while parent is not None:
		if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
			return False
		parent = parents.get(parent)
	return True


def testStyleCheckerSelfTests():
	'''Prove the checker catches bad indentation, names and docstrings.'''
	assert checkSource("'''ok'''\ndef goodName():\n\t'''ok'''\n\tvalue = 1\n") == []
	assert checkSource("'''ok'''\ndef snake_func():\n\t'''ok'''\n")
	assert checkSource("'''ok'''\n\ndef goodName():\n    '''ok'''\n")
	assert checkSource('"""bad"""\ndef goodName():\n\t"""bad"""\n')
	assert checkSource("'''ok'''\ndef goodName():\n\tpass\n")
	assert checkSource("'''ok'''\ndef goodName(camelVar):\n\t'''ok'''\n\tcamelVar = 1\n")


def testRepositoryStyle():
	'''Check all project Python files against the required source conventions.'''
	files = [path for path in ROOT.rglob('*.py') if '.git' not in path.parts]
	failures = {str(path.relative_to(ROOT)): checkSource(path.read_text()) for path in files}
	failures = {path: issues for path, issues in failures.items() if issues}
	assert not failures, failures
