bl_info = {
    "name": "Custom Orientation Highlighter",
    "author": "Antigravity AI & RogenLothena",
    "version": (2, 5, 2),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > Orientation Highlighter Tab",
    "description": "Highlights selected faces and edges with custom colors based on their transform orientations to prevent mesh confusion.",
    "doc_url": "https://github.com/RogenLothena/my-addon-repo/wiki",
    "tracker_url": "https://github.com/RogenLothena/my-addon-repo/issues",
    "category": "3D View",
}
import bpy
import bmesh
import gpu
import colorsys
import json
from gpu_extras.batch import batch_for_shader

# ============================================================
# STATE
# ============================================================

_draw_handle = None
_HUE = 0.0

# ============================================================
# EXTENDED COLOR & PALETTE
# ============================================================

def _color():
    global _HUE
    h = _HUE % 1.0
    _HUE += 0.13
    r, g, b = colorsys.hsv_to_rgb(h, 0.85, 1.0)
    return (r, g, b, 0.80)

def _color_to_name(color):
    r, g, b = color[:3]
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    h *= 360

    if s < 0.2:
        return "Neutral"
    if 0 <= h < 20 or h >= 340:
        return "Red"
    if 20 <= h < 45:
        return "Orange"
    if 45 <= h < 70:
        return "Yellow"
    if 70 <= h < 140:
        return "Green"
    if 140 <= h < 195:
        return "Cyan"
    if 195 <= h < 255:
        return "Blue"
    if 255 <= h < 340:
        return "Purple"

    return "Custom"

# ============================================================
# DATA ISOLATION (THE CORE SOLUTION)
# ============================================================

def get_mesh_tags(obj):
    if not obj or not obj.data or "orient_tags" not in obj.data:
        return {}
    try:
        return json.loads(obj.data["orient_tags"])
    except:
        return {}

def set_mesh_tags(obj, tags):
    if obj and obj.data:
        obj.data["orient_tags"] = json.dumps(tags)

# ============================================================
# BLENDER SOURCE OF TRUTH
# ============================================================

def _blender_orientation_names(scene):
    names = []
    for s in scene.transform_orientation_slots:
        if s.type == 'CUSTOM' and s.custom_orientation:
            names.append(s.custom_orientation.name)
    return names

def _blender_unique_name(existing, base):
    if base not in existing:
        return base

    i = 1
    while f"{base}.{i:03d}" in existing:
        i += 1

    return f"{base}.{i:03d}"

# ============================================================
# REDRAW
# ============================================================

def _redraw():
    wm = bpy.context.window_manager
    for w in wm.windows:
        for a in w.screen.areas:
            if a.type == "VIEW_3D":
                a.tag_redraw()

# ============================================================
# CAPTURE
# ============================================================

def _capture(obj):
    bm = bmesh.from_edit_mesh(obj.data)

    faces = [f.index for f in bm.faces if f.select]
    if faces:
        return ("FACE", faces)

    edges = [e.index for e in bm.edges if e.select]
    if edges:
        return ("EDGE", edges)

    return None

# ============================================================
# DRAW GEOMETRY
# ============================================================

def _coords(obj, tag):
    mesh = obj.data
    mw = obj.matrix_world
    out = []

    if tag["type"] == "EDGE":
        for i in tag["ids"]:
            if i >= len(mesh.edges):
                continue
            e = mesh.edges[i]
            v0 = mw @ mesh.vertices[e.vertices[0]].co
            v1 = mw @ mesh.vertices[e.vertices[1]].co
            out += [tuple(v0), tuple(v1)]
    else:
        for i in tag["ids"]:
            if i >= len(mesh.polygons):
                continue

            poly = mesh.polygons[i]
            normal = (mw.to_3x3() @ poly.normal).normalized()

            verts = [mw @ mesh.vertices[v].co for v in poly.vertices]
            verts = [v + normal * 0.0001 for v in verts]

            for j in range(1, len(verts) - 1):
                out += [
                    tuple(verts[0]),
                    tuple(verts[j]),
                    tuple(verts[j + 1]),
                ]
    return out

# ============================================================
# DRAW
# ============================================================

def _draw():
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("LESS_EQUAL")
    gpu.state.depth_mask_set(True)
    gpu.state.line_width_set(3.0)

    for obj in bpy.context.scene.objects:
        if obj.type != 'MESH' or obj.hide_viewport: 
            continue
        
        if "orient_tags" not in obj.data: 
            continue
            
        tags = get_mesh_tags(obj)
        if not tags: 
            continue

        for t in tags.values():
            coords = _coords(obj, t)
            if not coords:
                continue

            col = t["color"]

            if t["type"] == "EDGE":
                batch = batch_for_shader(shader, "LINES", {"pos": coords})
                shader.bind()
                shader.uniform_float("color", col)
                batch.draw(shader)
            else:
                batch = batch_for_shader(shader, "TRIS", {"pos": coords})
                shader.bind()
                shader.uniform_float("color", (col[0], col[1], col[2], 0.30))
                batch.draw(shader)

    gpu.state.blend_set("NONE")
    gpu.state.depth_test_set("NONE")
    gpu.state.depth_mask_set(False)
    gpu.state.line_width_set(1.0)

# ============================================================
# CREATE TAG
# ============================================================

class ORIENT_OT_create(bpy.types.Operator):
    bl_idname = "orient.create_tag"
    bl_label = "Create Tag"

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.mode == "EDIT"

    def execute(self, context):
        obj = context.active_object
        snap = _capture(obj)

        if not snap:
            return {"CANCELLED"}

        scene = context.scene

        blender_existing = _blender_orientation_names(scene)
        addon_existing = []
        for o in scene.objects:
            if o.type == 'MESH':
                addon_existing.extend([t["name"] for t in get_mesh_tags(o).values()])
                
        all_existing = list(set(blender_existing + addon_existing))

        color = _color()
        base = _color_to_name(color)
        name = _blender_unique_name(all_existing, base)

        bpy.ops.transform.create_orientation(
            name=name,
            use_view=False,
            use=True,
            overwrite=False
        )

        actual_name = scene.transform_orientation_slots[0].custom_orientation.name

        current_tags = get_mesh_tags(obj)
        tag_id = f"tag_{len(current_tags)}"
        
        current_tags[tag_id] = {
            "name": actual_name,
            "type": snap[0],
            "ids": snap[1],
            "color": color,
        }

        set_mesh_tags(obj, current_tags)
        _redraw()
        return {"FINISHED"}

# ============================================================
# DELETE TAG
# ============================================================

class ORIENT_OT_delete(bpy.types.Operator):
    bl_idname = "orient.delete_tag"
    bl_label = "Delete"

    key: bpy.props.StringProperty()

    def execute(self, context):
        obj = context.active_object
        if obj:
            current_tags = get_mesh_tags(obj)
            if self.key in current_tags:
                del current_tags[self.key]
                set_mesh_tags(obj, current_tags)

        _redraw()
        return {"FINISHED"}

# ============================================================
# CLEAR ALL
# ============================================================

class ORIENT_OT_clear_all(bpy.types.Operator):
    bl_idname = "orient.clear_all"
    bl_label = "Reset Colors"
    
    def execute(self, context):
        global _HUE
        _HUE = 0.0
        for obj in context.scene.objects:
            if obj.type == 'MESH' and "orient_tags" in obj.data:
                del obj.data["orient_tags"]
        _redraw()
        return {'FINISHED'}

# ============================================================
# PANEL
# ============================================================

class ORIENT_PT_panel(bpy.types.Panel):
    bl_label = "Orientation Highlighter"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Orientation Highlighter"

    def draw(self, context):
        layout = self.layout
        
        # UI Warning Box
        box = layout.box()
        box.alert = True
        box.label(text="NOTE:", icon='ERROR')
        
        col = box.column()
        col.scale_y = 0.95
        
        lines = [
            "Ensure you are in Edit Mode to create colors properly.",
            
        ]
        
        for line in lines:
            if line == "":
                col.separator()
            else:
                row = col.row()
                row.ui_units_x = 0  
                label = row.label(text=line)
        
        layout.separator()
        
        layout.operator("orient.create_tag")
        layout.operator("orient.clear_all", icon='TRASH', text="Reset Colors")

        obj = context.active_object
        if obj and obj.type == 'MESH':
            tags = get_mesh_tags(obj)
            for k, t in tags.items():
                row = layout.row()
                row.label(text=t["name"])
                
                op = row.operator(
                    "orient.delete_tag",
                    text="",
                    icon="TRASH"
                )
                op.key = k
        else:
            layout.label(text="Select a mesh object")

# ============================================================
# REGISTER
# ============================================================

_classes = [
    ORIENT_OT_create,
    ORIENT_OT_delete,
    ORIENT_OT_clear_all,
    ORIENT_PT_panel,
]

def register():
    global _draw_handle
    for c in _classes:
        bpy.utils.register_class(c)

    if _draw_handle is None:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw, (), "WINDOW", "POST_VIEW"
        )

def unregister():
    global _draw_handle
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)

    if _draw_handle:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, "WINDOW")
        _draw_handle = None

if __name__ == "__main__":
    register()