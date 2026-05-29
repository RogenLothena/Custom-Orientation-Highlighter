bl_info = {
    "name": "Custom Orientation Highlighter",
    "author": "Antigravity AI & RogenLothena",
    "version": "3.4.0",
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > Orientation Highlighter Tab",
    "description": "Custom orientation highlighter and management tool.",
    "doc_url": "https://github.com/RogenLothena/Custom-Orientation-Highlighter",
    "tracker_url": "https://github.com/RogenLothena/Custom-Orientation-Highlighter/issues",
    "category": "3D View",
}
import bpy
import bmesh
import gpu
import colorsys
import mathutils
from gpu_extras.batch import batch_for_shader

# ============================================================
# STATE & CONFIG
# ============================================================

_draw_handle = None
_HUE = 0.0

# ============================================================
# COLOR & PALETTE
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
# SAF BLENDER NATIVE DATA ISOLATION (NO MORE JSON!)
# ============================================================

def get_mesh_tags(obj):
    """Blender'ın kendi iç sözlük yapısını kullanarak verileri saf haliyle okur."""
    if not obj or "orient_tags" not in obj:
        return {}
    
    tags_dict = {}
    try:
        native_dict = obj["orient_tags"]
        for k in native_dict.keys():
            t_data = native_dict[k]
            
            raw_coords = t_data.get("coords", [])
            flat_coords = []
            for c in raw_coords:
                flat_coords.append((c[0], c[1], c[2]))
                
            tags_dict[k] = {
                "name": t_data.get("name", "Unknown"),
                "type": t_data.get("type", "FACE"),
                "coords": flat_coords,
                "color": tuple(t_data.get("color", (1, 1, 1, 1))),
                "owner_obj": t_data.get("owner_obj", "")
            }
    except Exception as e:
        print(f"Highlighter Read Error: {e}")
        return {}
    return tags_dict

def set_mesh_tags(obj, tags):
    """Veriyi stringe çevirmeden doğrudan Blender nesne yapısına kaydeder."""
    if obj:
        obj["orient_tags"] = tags

def _blender_orientation_names(scene):
    if not scene:
        return []
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

def _redraw():
    wm = bpy.context.window_manager
    for w in wm.windows:
        for a in w.screen.areas:
            if a.type == "VIEW_3D":
                a.tag_redraw()

# ============================================================
# GEOMETRY CAPTURE
# ============================================================

def _capture_absolute_geom(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    mw = obj.matrix_world
    coords = []
    
    normal_matrix = mw.to_3x3().inverted().transposed()
    
    selected_faces = [f for f in bm.faces if f.select]
    if selected_faces:
        ref_face = selected_faces[0]
        world_normal = (normal_matrix @ ref_face.normal).normalized()
        
        world_tangent = (mw.to_3x3() @ ref_face.verts[0].co.normalized()).cross(world_normal).normalized()
        if world_tangent.length < 0.001:
            world_tangent = world_normal.orthogonal()
        world_bitangent = world_normal.cross(world_tangent).normalized()
        
        rot_matrix = mathutils.Matrix((world_tangent, world_bitangent, world_normal)).transposed()

        for f in selected_faces:
            normal = f.normal
            verts = [tuple(mw @ (v.co + normal * 0.0001)) for v in f.verts]
            for j in range(1, len(verts) - 1):
                coords += [verts[0], verts[j], verts[j + 1]]
                
        return ("FACE", coords, rot_matrix)

    selected_edges = [e for e in bm.edges if e.select]
    if selected_edges:
        ref_edge = selected_edges[0]
        v0_w = mw @ ref_edge.verts[0].co
        v1_w = mw @ ref_edge.verts[1].co
        edge_dir = (v1_w - v0_w).normalized()
        
        world_tangent = edge_dir
        world_normal = world_tangent.orthogonal()
        world_bitangent = world_normal.cross(world_tangent).normalized()
        
        rot_matrix = mathutils.Matrix((world_tangent, world_bitangent, world_normal)).transposed()

        for e in selected_edges:
            coords += [tuple(mw @ e.verts[0].co), tuple(mw @ e.verts[1].co)]
            
        return ("EDGE", coords, rot_matrix)

    return None

# ============================================================
# RENDER ENGINE (SAF VE HATASIZ)
# ============================================================

def _draw():
    context = bpy.context
    obj = context.active_object
    if not obj or obj.type != 'MESH' or obj.hide_viewport:
        return

    tags = get_mesh_tags(obj)
    if not tags: 
        return

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("LESS_EQUAL")
    gpu.state.depth_mask_set(True)
    gpu.state.line_width_set(3.0)

    for tag_id, t in tags.items():
        if t.get("owner_obj") and t["owner_obj"] != obj.name:
            continue

        world_coords = t.get("coords", [])
        if not world_coords:
            continue

        col = t["color"]

        try:
            if t["type"] == "EDGE":
                batch = batch_for_shader(shader, "LINES", {"pos": world_coords})
                shader.bind()
                shader.uniform_float("color", col)
                batch.draw(shader)
            else:
                batch = batch_for_shader(shader, "TRIS", {"pos": world_coords})
                shader.bind()
                shader.uniform_float("color", (col[0], col[1], col[2], 0.30))
                batch.draw(shader)
        except Exception as e:
            print(f"Shader Error: {e}")

    gpu.state.blend_set("NONE")
    gpu.state.depth_test_set("NONE")
    gpu.state.depth_mask_set(False)
    gpu.state.line_width_set(1.0)

# ============================================================
# OPERATORS
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
        obj.update_from_editmode()
        
        snap = _capture_absolute_geom(obj)
        if not snap:
            return {"CANCELLED"}

        geom_type, coords, calculated_matrix = snap
        scene = context.scene

        blender_existing = _blender_orientation_names(scene)
        addon_existing = []
        for o in scene.objects:
            addon_existing.extend([t["name"] for t in get_mesh_tags(o).values()])
                
        all_existing = list(set(blender_existing + addon_existing))

        color = _color()
        base = _color_to_name(color)
        name = _blender_unique_name(all_existing, base)

        bpy.ops.transform.create_orientation(name=name, use=True, overwrite=True)
        
        custom_orient = scene.transform_orientation_slots[0].custom_orientation
        if custom_orient:
            custom_orient.matrix = calculated_matrix

        actual_name = custom_orient.name

        current_tags = get_mesh_tags(obj)
        tag_id = f"tag_{len(current_tags)}"
        
        current_tags[tag_id] = {
            "name": actual_name,
            "type": geom_type,
            "coords": coords, 
            "color": color,
            "owner_obj": obj.name
        }

        set_mesh_tags(obj, current_tags)
        _redraw()
        return {"FINISHED"}

class ORIENT_OT_delete(bpy.types.Operator):
    bl_idname = "orient.delete_tag"
    bl_label = "Delete"

    key: bpy.props.StringProperty()

    def execute(self, context):
        obj = context.active_object
        if obj:
            current_tags = get_mesh_tags(obj)
            if self.key in current_tags:
                tag_data = current_tags[self.key]
                target_orientation_name = tag_data.get("name", "")

                if target_orientation_name:
                    slot = context.scene.transform_orientation_slots[0]
                    old_type = slot.type
                    old_custom_name = slot.custom_orientation.name if (slot.type == 'CUSTOM' and slot.custom_orientation) else None

                    if old_custom_name == target_orientation_name:
                        slot.type = 'GLOBAL'
                        old_type = 'GLOBAL'
                        old_custom_name = None

                    try:
                        bpy.ops.transform.select_orientation(orientation=target_orientation_name)
                        bpy.ops.transform.delete_orientation()
                    except:
                        pass
                    
                    if old_type == 'CUSTOM' and old_custom_name:
                        try:
                            slot.use_orientation_by_name(old_custom_name)
                        except:
                            slot.type = 'GLOBAL'
                    else:
                        try:
                            slot.type = old_type
                        except:
                            slot.type = 'GLOBAL'

                del current_tags[self.key]
                set_mesh_tags(obj, current_tags)

        _redraw()
        return {"FINISHED"}

class ORIENT_OT_clear_all(bpy.types.Operator):
    bl_idname = "orient.clear_all"
    bl_label = "Reset Colors"
    
    def execute(self, context):
        global _HUE
        _HUE = 0.0
        
        orientations_to_remove = set()
        for obj in context.scene.objects:
            tags = get_mesh_tags(obj)
            for t in tags.values():
                if "name" in t:
                    orientations_to_remove.add(t["name"])
            
            if "orient_tags" in obj:
                del obj["orient_tags"]

        for slot in context.scene.transform_orientation_slots:
            slot.type = 'GLOBAL'

        for name in orientations_to_remove:
            try:
                bpy.ops.transform.select_orientation(orientation=name)
                bpy.ops.transform.delete_orientation()
            except:
                pass

        _redraw()
        return {'FINISHED'}

class ORIENT_OT_delete_all_custom(bpy.types.Operator):
    """Blender sahnesindeki tüm harici kullanıcı yönelimlerini (Custom Orientations) temizler."""
    bl_idname = "orient.delete_all_custom"
    bl_label = "Purge All Custom Orientations"
    bl_description = "Force delete every single custom transform orientation from the top bar dropdown"
    
    def execute(self, context):
        # Aktif yönelim slotlarını geçici olarak GLOBAL yapıyoruz ki açıkta kilit kalmasın
        for slot in context.scene.transform_orientation_slots:
            slot.type = 'GLOBAL'
            
        # Sahnedeki kayıtlı tüm custom orientation adlarını toplayalım
        custom_names = _blender_orientation_names(context.scene)
        
        # Eğer aktif listede bir şey bulamadıysak alternatif olarak sahnede objelerde kayıtlı isimleri de ekle
        for obj in context.scene.objects:
            tags = get_mesh_tags(obj)
            for t in tags.values():
                if "name" in t and t["name"] not in custom_names:
                    custom_names.append(t["name"])
        
        # Toplanan tüm custom orientation'ları Blender içinden kaldır
        for name in custom_names:
            try:
                bpy.ops.transform.select_orientation(orientation=name)
                bpy.ops.transform.delete_orientation()
            except:
                pass
        
        _redraw()
        self.report({'INFO'}, "All Custom Transform Orientations purged successfully.")
        return {'FINISHED'}

# ============================================================
# PANEL
# ============================================================

class ORIENT_PT_panel(bpy.types.Panel):
    bl_label = "Custom Orientation Highlighter"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Custom Orientations" 

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        

        col = layout.column(align=True)
        col.label(text="Actions", icon='TOOL_SETTINGS')
        col.operator("orient.create_tag", text="Add New Highlight", icon='ADD')
        col.operator("orient.clear_all", text="Reset Colors", icon='FILE_REFRESH')
        
        layout.separator()


        if obj and obj.type == 'MESH':
            tags = get_mesh_tags(obj)
            if tags:
                layout.label(text="Active Highlights:")
                box = layout.box()
                for k, t in tags.items():
                    row = box.row(align=True)
                    row.label(text=t["name"])
                    op = row.operator("orient.delete_tag", text="", icon="X")
                    op.key = k
            else:
                layout.label(text="No highlights on this object.", icon='INFO')
        else:
            layout.label(text="Select a mesh to manage.", icon='MESH_DATA')
            

        layout.separator(factor=2.0)
        box = layout.box()
        box.label(text="Danger Zone", icon='ERROR')
        box.operator("orient.delete_all_custom", text="Clear All Orientations", icon='TRASH')

# ============================================================
# REGISTER
# ============================================================

_classes = [
    ORIENT_OT_create,
    ORIENT_OT_delete,
    ORIENT_OT_clear_all,
    ORIENT_OT_delete_all_custom,
    ORIENT_PT_panel,
]

def register():
    for c in _classes:
        bpy.utils.register_class(c)

    global _draw_handle
    if _draw_handle is None:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw, (), "WINDOW", "POST_VIEW"
        )

def unregister():
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)

    global _draw_handle
    if _draw_handle:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, "WINDOW")
        _draw_handle = None

if __name__ == "__main__":
    register()
   


