"""
Edge Manager - Core management functionality for edge nodes
"""

class EdgeManager:
    """Core edge node manager"""
    
    def __init__(self):
        self.nodes = []
        
    def add_node(self, node):
        """Add a node to management"""
        self.nodes.append(node)
        
    def remove_node(self, node):
        """Remove a node from management"""
        if node in self.nodes:
            self.nodes.remove(node)
            
    def get_nodes(self):
        """Get all managed nodes"""
        return self.nodes
