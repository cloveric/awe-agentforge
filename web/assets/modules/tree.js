export async function loadProjectTreeData({ projectPath, force = false, state, api }) {
  if (!projectPath) {
    return null;
  }
  if (!force && state.treeByProject.has(projectPath)) {
    return state.treeByProject.get(projectPath) || null;
  }
  const pathParam = encodeURIComponent(projectPath);
  const data = await api(
    `/api/workspace-tree?workspace_path=${pathParam}&max_depth=4&max_entries=800`,
    { healthImpact: false },
  );
  state.treeByProject.set(projectPath, data);
  return data;
}

export function treeOpenStateForProject({ projectPath, state, normalizeProjectPath }) {
  const key = normalizeProjectPath(projectPath);
  if (!state.treeOpenByProject.has(key)) {
    state.treeOpenByProject.set(key, new Map());
  }
  return state.treeOpenByProject.get(key);
}

export function buildProjectTreeHierarchy({ tree, normalizeProjectPath, projectName, treeNodeLabel }) {
  const workspace = normalizeProjectPath(tree.workspace_path).replace(/\\/g, '/');
  const root = {
    path: workspace,
    kind: 'dir',
    depth: -1,
    name: projectName(workspace),
    children: [],
  };
  const stack = [root];
  for (const raw of tree.nodes || []) {
    const path = normalizeProjectPath(raw.path).replace(/\\/g, '/');
    if (!path) continue;
    const depth = Math.max(0, Number(raw.depth || 0));
    const kind = raw.kind === 'dir' ? 'dir' : 'file';
    if (depth === 0 && path === workspace) {
      continue;
    }
    const node = {
      path,
      kind,
      depth,
      name: treeNodeLabel(path),
      children: [],
    };
    while (stack.length > depth + 1) {
      stack.pop();
    }
    const parent = stack[stack.length - 1] || root;
    parent.children.push(node);
    if (kind === 'dir') {
      stack.push(node);
    }
  }
  return root.children;
}

export function renderProjectTreeBranch({ nodes, depth, dirState }) {
  const branch = document.createElement('ul');
  branch.className = depth > 0 ? 'tree-branch nested' : 'tree-branch';
  for (const node of nodes) {
    const item = document.createElement('li');
    item.className = `tree-item ${node.kind}`;
    if (node.kind === 'dir') {
      const details = document.createElement('details');
      details.className = 'tree-folder';
      details.dataset.path = node.path;
      details.open = dirState.has(node.path) ? !!dirState.get(node.path) : depth < 1;
      details.addEventListener('toggle', () => {
        dirState.set(node.path, details.open);
      });

      const summary = document.createElement('summary');
      summary.className = 'tree-entry dir';
      summary.title = node.path;

      const caret = document.createElement('span');
      caret.className = 'tree-caret';
      caret.textContent = '>';

      const icon = document.createElement('span');
      icon.className = 'tree-icon';
      icon.textContent = 'D';

      const name = document.createElement('span');
      name.className = 'tree-name';
      name.textContent = node.name;

      summary.appendChild(caret);
      summary.appendChild(icon);
      summary.appendChild(name);
      details.appendChild(summary);

      if (node.children.length) {
        details.appendChild(renderProjectTreeBranch({ nodes: node.children, depth: depth + 1, dirState }));
      } else {
        const empty = document.createElement('div');
        empty.className = 'tree-leaf-empty';
        empty.textContent = '(empty)';
        details.appendChild(empty);
      }
      item.appendChild(details);
    } else {
      const entry = document.createElement('div');
      entry.className = 'tree-entry file';
      entry.title = node.path;

      const pad = document.createElement('span');
      pad.className = 'tree-pad';
      pad.textContent = ' ';

      const icon = document.createElement('span');
      icon.className = 'tree-icon';
      icon.textContent = 'F';

      const name = document.createElement('span');
      name.className = 'tree-name';
      name.textContent = node.name;

      entry.appendChild(pad);
      entry.appendChild(icon);
      entry.appendChild(name);
      item.appendChild(entry);
    }
    branch.appendChild(item);
  }
  return branch;
}

export function setProjectTreeExpansion({
  open,
  state,
  normalizeProjectPath,
  treeOpenStateForFn,
  projectTreeEl,
}) {
  const project = normalizeProjectPath(state.selectedProject);
  const dirState = treeOpenStateForFn(project);
  const folders = projectTreeEl.querySelectorAll('details.tree-folder');
  folders.forEach((folder) => {
    folder.open = open;
    const path = folder.dataset.path;
    if (path) {
      dirState.set(path, open);
    }
  });
}

export function renderProjectTreePanel({
  tree,
  el,
  buildTreeHierarchyFn,
  renderTreeBranchFn,
  treeOpenStateForFn,
}) {
  el.projectTree.innerHTML = '';
  if (!tree || !Array.isArray(tree.nodes)) {
    el.projectTreeMeta.textContent = 'No project tree available.';
    el.projectTree.innerHTML = '<div class="tree-empty">Select a project to load structure.</div>';
    if (el.expandTreeBtn) el.expandTreeBtn.disabled = true;
    if (el.collapseTreeBtn) el.collapseTreeBtn.disabled = true;
    return;
  }

  const extra = tree.truncated ? ' (truncated)' : '';
  el.projectTreeMeta.textContent = `root=${tree.workspace_path} | entries=${tree.total_entries}${extra}`;
  const hierarchy = buildTreeHierarchyFn(tree);
  if (!hierarchy.length) {
    el.projectTree.innerHTML = '<div class="tree-empty">Project is empty.</div>';
    if (el.expandTreeBtn) el.expandTreeBtn.disabled = true;
    if (el.collapseTreeBtn) el.collapseTreeBtn.disabled = true;
    return;
  }
  const dirState = treeOpenStateForFn(tree.workspace_path);
  el.projectTree.appendChild(renderTreeBranchFn(hierarchy, 0, dirState));
  if (el.expandTreeBtn) el.expandTreeBtn.disabled = false;
  if (el.collapseTreeBtn) el.collapseTreeBtn.disabled = false;
}
