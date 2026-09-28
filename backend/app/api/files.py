"""Editable plugin sources with containment checks and local revision backups."""
import ast
import json
from pathlib import Path
from uuid import uuid4

EXTENSIONS={'.py','.js','.mjs','.cjs','.ps1','.sh'}


def script_path(directory,name):
    relative=Path(name)
    path=(directory/relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(directory.resolve()) or path.suffix.lower() not in EXTENSIONS or any(part.startswith('.') for part in relative.parts):
        raise ValueError('请选择任务目录内的脚本文件')
    return path


def files(directory):
    paths=[]
    for path in directory.rglob('*'):
        if path.is_file() and path.suffix.lower() in EXTENSIONS:
            relative=path.relative_to(directory)
            if any(part.startswith('.') or part=='node_modules' for part in relative.parts):
                continue
            try:
                script_path(directory,str(relative))
            except ValueError:
                continue
            paths.append(relative.as_posix())
            if len(paths)>=500:
                break
    return sorted(paths)


def read(directory,name):
    path=script_path(directory,name)
    if not path.is_file():
        return ''
    if path.stat().st_size>262144:
        raise ValueError('脚本大于 256 KiB，请在本机编辑器中处理')
    return path.read_text(encoding='utf-8-sig')


def save(directory,name,content,backup_dir):
    path=script_path(directory,name)
    if len(content.encode('utf-8'))>262144:
        raise ValueError('脚本大于 256 KiB')
    if path.suffix=='.py':
        try:
            ast.parse(content,filename=name)
        except SyntaxError as error:
            raise ValueError(f'Python 语法错误，第 {error.lineno} 行：{error.msg}') from None
    backup=None
    if path.exists():
        previous=read(directory,name)
        backup_dir.mkdir(parents=True,exist_ok=True)
        backup=uuid4().hex+'.json'
        (backup_dir/backup).write_text(json.dumps({'file':str(path),'content':previous},ensure_ascii=False),encoding='utf-8')
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    temporary.write_text(content,encoding='utf-8')
    temporary.replace(path)
    return backup
