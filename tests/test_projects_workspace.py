"""Projects workspace focused tests.

Tests ProjectsView, ProjectTree component, and ProjectsProvider integration.
"""

import tempfile
from pathlib import Path

import pytest

from personal_os.core.config import VaultConfig
from personal_os.core.vault import VaultManager
from personal_os.tui.providers.projects_provider import (
    ProjectsProvider,
    ProjectsData,
    ProjectDetail,
    ProjectDocument,
    create_projects_provider,
)
from personal_os.tui.components.project_tree import ProjectTree, ProjectTreeNode


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def isolated_vault():
    """Create a temporary vault with test project data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault_path = Path(tmpdir)
        config = VaultConfig(root=vault_path)
        vm = VaultManager(config)
        
        # Create test projects
        project1_dir = vm.config.projects_dir / "test-project-1"
        project1_dir.mkdir(parents=True)
        vm.write_file(project1_dir / "README.md", "# Test Project 1\n\nA test project for search.")
        vm.write_file(project1_dir / "Tasks.md", "# Tasks\n\n## Active Tasks\n- [ ] Fix the bug\n- [ ] Write tests")
        vm.write_file(project1_dir / "Progress.md", "# Progress\n\n**2026-01-15**: Fixed bug (2h)")
        
        project2_dir = vm.config.projects_dir / "test-project-2"
        project2_dir.mkdir(parents=True)
        vm.write_file(project2_dir / "README.md", "# Test Project 2\n\nAnother test project.")
        vm.write_file(project2_dir / "Tasks.md", "# Tasks\n\n## Active Tasks\n- [ ] Task one\n\n## Completed Tasks\n- [x] Done task")
        vm.write_file(project2_dir / "Progress.md", "# Progress\n\n**2026-01-10**: Started (1h)")
        
        yield vm


@pytest.fixture
def app_services(isolated_vault):
    """Mock AppServices for provider creation."""
    from dataclasses import dataclass
    
    @dataclass
    class MockAppServices:
        vault: VaultManager
        config: any = None
    
    return MockAppServices(vault=isolated_vault)


# --------------------------------------------------------------------------- #
# ProjectDocument Tests
# --------------------------------------------------------------------------- #

class TestProjectDocument:
    """Test ProjectDocument dataclass."""
    
    def test_project_document_creation(self):
        """Test ProjectDocument can be created."""
        doc = ProjectDocument(
            name="README",
            path=Path("/test/README.md"),
            exists=True,
            content="# Test\n\nContent here."
        )
        
        assert doc.name == "README"
        assert doc.exists is True
        assert doc.content == "# Test\n\nContent here."
    
    def test_preview_short(self):
        """Test preview returns full content for short content."""
        doc = ProjectDocument(
            name="README",
            path=Path("/test/README.md"),
            exists=True,
            content="Short content"
        )
        
        assert doc.preview() == "Short content"
    
    def test_preview_long(self):
        """Test preview truncates long content."""
        long_content = "A" * 200
        doc = ProjectDocument(
            name="README",
            path=Path("/test/README.md"),
            exists=True,
            content=long_content
        )
        
        preview = doc.preview(120)
        assert len(preview) <= 123  # 120 + "..."
        assert preview.endswith("...")


# --------------------------------------------------------------------------- #
# ProjectDetail Tests
# --------------------------------------------------------------------------- #

class TestProjectDetail:
    """Test ProjectDetail dataclass."""
    
    def test_project_detail_creation(self):
        """Test ProjectDetail can be created."""
        detail = ProjectDetail(
            name="test-project",
            path=Path("/test/test-project"),
            status="active",
            health="green",
            open_tasks=5,
            total_hours=10.5
        )
        
        assert detail.name == "test-project"
        assert detail.status == "active"
        assert detail.health == "green"
        assert detail.open_tasks == 5
        assert detail.total_hours == 10.5
    
    def test_get_document(self):
        """Test get_document returns correct document."""
        doc = ProjectDocument(name="README", path=Path("/test/README.md"), exists=True, content="Test")
        detail = ProjectDetail(
            name="test",
            path=Path("/test"),
            documents={"readme": doc}
        )
        
        result = detail.get_document("readme")
        assert result == doc
        
        result = detail.get_document("nonexistent")
        assert result is None
    
    def test_has_documents(self):
        """Test has_documents checks for content."""
        doc_empty = ProjectDocument(name="Empty", path=Path("/test/empty.md"), exists=True, content="")
        doc_content = ProjectDocument(name="Content", path=Path("/test/content.md"), exists=True, content="Real content")
        
        detail_empty = ProjectDetail(name="test", path=Path("/test"), documents={"empty": doc_empty})
        detail_with_content = ProjectDetail(name="test", path=Path("/test"), documents={"content": doc_content})
        
        assert detail_empty.has_documents() is False
        assert detail_with_content.has_documents() is True


# --------------------------------------------------------------------------- #
# ProjectsData Tests
# --------------------------------------------------------------------------- #

class TestProjectsData:
    """Test ProjectsData dataclass."""
    
    def test_projects_data_creation(self):
        """Test ProjectsData can be created."""
        detail = ProjectDetail(name="test", path=Path("/test"))
        data = ProjectsData(projects=[detail], selected_project="test")
        
        assert len(data.projects) == 1
        assert data.selected_project == "test"
    
    def test_get_project(self):
        """Test get_project finds project by name."""
        detail1 = ProjectDetail(name="project1", path=Path("/test/1"))
        detail2 = ProjectDetail(name="project2", path=Path("/test/2"))
        data = ProjectsData(projects=[detail1, detail2])
        
        result = data.get_project("project1")
        assert result == detail1
        
        result = data.get_project("nonexistent")
        assert result is None
    
    def test_get_selected(self):
        """Test get_selected returns selected project."""
        detail1 = ProjectDetail(name="project1", path=Path("/test/1"))
        detail2 = ProjectDetail(name="project2", path=Path("/test/2"))
        data = ProjectsData(projects=[detail1, detail2], selected_project="project2")
        
        result = data.get_selected()
        assert result == detail2


# --------------------------------------------------------------------------- #
# ProjectsProvider Tests
# --------------------------------------------------------------------------- #

class TestProjectsProvider:
    """Test ProjectsProvider data layer."""
    
    def test_provider_initialization(self, app_services):
        """Test ProjectsProvider initializes correctly."""
        provider = create_projects_provider(app_services)
        
        assert isinstance(provider, ProjectsProvider)
        assert provider.vault is not None
        assert provider.config is not None
    
    def test_get_data(self, app_services, isolated_vault):
        """Test get_data returns projects data."""
        provider = create_projects_provider(app_services)
        data = provider.get_data()
        
        assert isinstance(data, ProjectsData)
        assert len(data.projects) >= 2  # At least our two test projects
    
    def test_project_detail_structure(self, app_services, isolated_vault):
        """Test project detail has expected structure."""
        provider = create_projects_provider(app_services)
        data = provider.get_data()
        
        project = data.get_project("test-project-1")
        assert project is not None
        
        # Check standard fields
        assert project.name == "test-project-1"
        assert project.status in ("active", "planning", "blocked", "on-hold", "completed", "archived")
        assert project.health in ("green", "yellow", "red")
        assert isinstance(project.open_tasks, int)
        assert isinstance(project.total_hours, float)
        
        # Check documents exist
        assert "readme" in project.documents
        assert "tasks" in project.documents
        assert "progress" in project.documents
        assert "notes" in project.documents
        assert "decisions" in project.documents
        assert "research" in project.documents
        assert "meetings" in project.documents
        assert "timeline" in project.documents
        assert "architecture" in project.documents
    
    def test_select_project(self, app_services, isolated_vault):
        """Test select_project updates selected project."""
        provider = create_projects_provider(app_services)
        data = provider.get_data()
        
        # Select second project
        result = provider.select_project("test-project-2")
        assert result is True
        
        data = provider.get_data()
        assert data.selected_project == "test-project-2"
        
        # Select nonexistent
        result = provider.select_project("nonexistent")
        assert result is False
    
    def test_read_document(self, app_services, isolated_vault):
        """Test read_document reads specific file."""
        provider = create_projects_provider(app_services)
        
        content = provider.read_document("test-project-1", "readme")
        assert "Test Project 1" in content
        
        # Nonexistent document
        content = provider.read_document("test-project-1", "nonexistent")
        assert content == ""
    
    def test_refresh(self, app_services, isolated_vault):
        """Test refresh invalidates cache and returns new data."""
        provider = create_projects_provider(app_services)
        data1 = provider.get_data()
        
        # Create a new project
        isolated_vault.create_project("new-project", "New project")
        
        data2 = provider.refresh()
        assert len(data2.projects) == len(data1.projects) + 1


# --------------------------------------------------------------------------- #
# ProjectTree Component Tests
# --------------------------------------------------------------------------- #

class TestProjectTreeComponent:
    """Test ProjectTree UI component."""
    
    def test_project_tree_node_creation(self, isolated_vault):
        """Test ProjectTreeNode can be created."""
        from personal_os.tui.providers.projects_provider import ProjectDetail
        
        detail = ProjectDetail(
            name="test-project",
            path=Path("/test/test-project"),
            status="active",
            health="green",
            open_tasks=3,
            last_activity="2026-01-15"
        )
        
        node = ProjectTreeNode(detail, callback=lambda x: None)
        
        assert node.project == detail
        assert node.is_selected is False
    
    def test_project_tree_node_selected(self, isolated_vault):
        """Test ProjectTreeNode with selected state."""
        from personal_os.tui.providers.projects_provider import ProjectDetail
        
        detail = ProjectDetail(name="test", path=Path("/test"))
        node = ProjectTreeNode(detail, is_selected=True, callback=lambda x: None)
        
        assert node.is_selected is True
        assert "selected" in node.classes
    
    def test_project_tree_initialization(self, isolated_vault):
        """Test ProjectTree initializes correctly."""
        tree = ProjectTree(on_select=lambda x: None)
        
        assert tree._projects == []
        assert tree._selected_project is None
        assert tree.on_select is not None
    
    def test_update_data(self, app_services, isolated_vault):
        """Test update_data refreshes tree with new data."""
        from personal_os.tui.providers.projects_provider import ProjectsProvider
        
        provider = create_projects_provider(app_services)
        data = provider.get_data()
        
        tree = ProjectTree(on_select=lambda x: None)
        tree.update_data(data)
        
        assert len(tree._projects) == len(data.projects)
        assert tree._selected_project == data.selected_project
    
    def test_select_project_programmatically(self, app_services, isolated_vault):
        """Test select_project selects project in tree."""
        from personal_os.tui.providers.projects_provider import ProjectsProvider
        
        provider = create_projects_provider(app_services)
        data = provider.get_data()
        
        tree = ProjectTree(on_select=lambda x: None)
        tree.update_data(data)
        
        # Select second project
        result = tree.select_project("test-project-2")
        assert result is True
        assert tree._selected_project == "test-project-2"
        
        # Select nonexistent
        result = tree.select_project("nonexistent")
        assert result is False
    
    def test_get_selected(self, app_services, isolated_vault):
        """Test get_selected returns selected project detail."""
        from personal_os.tui.providers.projects_provider import ProjectsProvider
        
        provider = create_projects_provider(app_services)
        data = provider.get_data()
        
        tree = ProjectTree(on_select=lambda x: None)
        tree.update_data(data)
        
        selected = tree.get_selected()
        assert selected is not None
        assert selected.name == data.selected_project


# --------------------------------------------------------------------------- #
# ProjectsView Integration Tests
# --------------------------------------------------------------------------- #

class TestProjectsViewIntegration:
    """Test ProjectsView integration with provider and components."""
    
    def test_view_initialization(self, app_services):
        """Test ProjectsView initializes correctly."""
        from personal_os.tui.views.projects import ProjectsView
        from personal_os.tui.services import AppServices
        
        view = ProjectsView(app_services)
        
        assert view.view_id == "projects"
        assert view.title == "Projects"
        assert view._provider is not None
    
    def test_view_on_activate(self, app_services):
        """Test ProjectsView on_activate loads data."""
        from personal_os.tui.views.projects import ProjectsView
        
        view = ProjectsView(app_services)
        view.on_activate()
        
        assert view._data is not None
        assert isinstance(view._data, ProjectsData)
    
    def test_view_refresh_content(self, app_services):
        """Test refresh_content reloads data."""
        from personal_os.tui.views.projects import ProjectsView
        
        view = ProjectsView(app_services)
        view.on_activate()
        
        # Just ensure it doesn't error
        import asyncio
        asyncio.run(view.refresh_content())
        
        assert view._data is not None


# --------------------------------------------------------------------------- #
# Handle Project Selection Tests
# --------------------------------------------------------------------------- #

class TestProjectSelection:
    """Test project selection handling."""
    
    def test_handle_project_select(self, app_services, isolated_vault):
        """Test _handle_project_select updates view."""
        from personal_os.tui.views.projects import ProjectsView
        
        view = ProjectsView(app_services)
        view.on_activate()
        
        # Handle selection
        view._handle_project_select("test-project-2")
        
        assert view._data.selected_project == "test-project-2"
        assert view._data.get_selected().name == "test-project-2"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])