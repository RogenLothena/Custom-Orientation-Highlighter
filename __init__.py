bl_info = {
    "name": "Custom Orientation Axis",
    "author": "Antigravity AI & RogenLothena",
    "version": "7.1.1",
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > Custom Orientations",
    "description": "Custom orientation axis and management tool.",
    "category": "3D View",
}

import bpy
import bmesh
import gpu
import mathutils
import blf
import uuid 
import time
from bpy_extras.view3d_utils import location_3d_to_region_2d, region_2d_to_vector_3d, region_2d_to_origin_3d
from gpu_extras.batch import batch_for_shader

_batch_cache = {}
handle_store = bpy.app.driver_namespace.setdefault("custom_orient_handles", {})

def get_all_custom_orientations():
    custom_types = []
    if not bpy.context.scene.transform_orientation_slots:
        return custom_types
    try:
        bpy.context.scene.transform_orientation_slots[0].type = "___HACK_TO_GET_LIST___"
    except TypeError as e:
        s = str(e)
        if "(" in s and ")" in s:
            t_str = s[s.find("(")+1 : s.rfind(")")]
            items = [x.strip(" '\"") for x in t_str.split(",")]
            builtins = {'GLOBAL', 'LOCAL', 'NORMAL', 'GIMBAL', 'VIEW', 'CURSOR', 'PARENT'}
            custom_types = [i for i in items if i not in builtins]
    return custom_types

def get_mesh_tags(obj):
    if not obj or "orient_tags" not in obj: return {}
    cleaned_tags = {}
    needs_update = False
    
    try:
        native_dict = obj["orient_tags"]
        if hasattr(native_dict, "to_dict"):
            native_dict = native_dict.to_dict()

        for k, t_data in native_dict.items():
            if t_data.get("owner_obj", "") == obj.name:
                cleaned_tags[k] = {
                    "name": t_data.get("name", "Unknown"),
                    "type": t_data.get("type", "FACE"),
                    "center": tuple(t_data.get("center", (0,0,0))),
                    "axis_x": tuple(t_data.get("axis_x", (1,0,0))),
                    "axis_y": tuple(t_data.get("axis_y", (0,1,0))),
                    "axis_z": tuple(t_data.get("axis_z", (0,0,1))),
                    "owner_obj": obj.name,
                    "in_front": t_data.get("in_front", False)
                }
            else:
                needs_update = True 
                
        if needs_update:
            obj["orient_tags"] = cleaned_tags
            global _batch_cache
            _batch_cache.clear()
    except:
        return {}
    return cleaned_tags

def set_mesh_tags(obj, tags):
    if obj:
        obj["orient_tags"] = tags
        _batch_cache.clear()

def _redraw():
    for w in bpy.context.window_manager.windows:
        for a in w.screen.areas:
            if a.type == "VIEW_3D": a.tag_redraw()

def _get_3d_override():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                for region in area.regions:
                    if region.type == 'WINDOW':
                        return {'window': window, 'screen': window.screen, 'area': area, 'region': region}
    return None

def _get_drawable_objects(context):
    if context.mode == 'EDIT_MESH' and context.edit_object:
        return [context.edit_object]
    return [o for o in context.selected_objects if o.type == 'MESH']

def _is_visible(context, region, rv3d, coord_3d, in_front):
    if in_front: 
        return True 
        
    depsgraph = context.evaluated_depsgraph_get()
    view_vector = region_2d_to_vector_3d(region, rv3d, location_3d_to_region_2d(region, rv3d, coord_3d))
    ray_origin = region_2d_to_origin_3d(region, rv3d, location_3d_to_region_2d(region, rv3d, coord_3d))
    
    target_dist = (coord_3d - ray_origin).length
    hit, location, normal, index, hit_obj, matrix = context.scene.ray_cast(depsgraph, ray_origin, view_vector)
    
    if hit:
        hit_dist = (location - ray_origin).length
        if hit_dist < target_dist - 0.05:
            return False
            
    return True

def _draw_3d():
    context = bpy.context
    global _batch_cache
    shader_lines = gpu.shader.from_builtin("SMOOTH_COLOR")
    
    gpu.state.blend_set("ALPHA")
    gpu.state.line_width_set(2.0)

    axis_length = 0.6 
    valid_cache_keys = set()
    drawn_tags = set()

    for obj in _get_drawable_objects(context):
        tags = get_mesh_tags(obj)
        if not tags: continue

        for tag_id, t in tags.items():
            if tag_id in drawn_tags: continue
            drawn_tags.add(tag_id)

            if "center" not in t or t["center"] == (0,0,0): continue

            cache_key = f"{obj.name}_{tag_id}"
            valid_cache_keys.add(cache_key)

            if cache_key not in _batch_cache:
                c = mathutils.Vector(t["center"])
                x = c + mathutils.Vector(t["axis_x"]) * axis_length
                y = c + mathutils.Vector(t["axis_y"]) * axis_length
                z = c + mathutils.Vector(t["axis_z"]) * axis_length

                coords = [c, x, c, y, c, z]
                colors = [
                    (1.0, 0.2, 0.3, 1.0), (1.0, 0.2, 0.3, 1.0), 
                    (0.5, 0.9, 0.2, 1.0), (0.5, 0.9, 0.2, 1.0), 
                    (0.2, 0.5, 1.0, 1.0), (0.2, 0.5, 1.0, 1.0)  
                ]

                batch = batch_for_shader(shader_lines, "LINES", {"pos": coords, "color": colors})
                _batch_cache[cache_key] = batch

            try:
                batch = _batch_cache[cache_key]
                if t.get("in_front", False):
                    gpu.state.depth_test_set("NONE")
                else:
                    gpu.state.depth_test_set("LESS_EQUAL")

                shader_lines.bind()
                batch.draw(shader_lines)
            except: pass

    keys_to_remove = [k for k in _batch_cache.keys() if k not in valid_cache_keys]
    for k in keys_to_remove: del _batch_cache[k]

    gpu.state.blend_set("NONE")
    gpu.state.depth_test_set("NONE") 
    gpu.state.line_width_set(1.0)

def _draw_2d():
    context = bpy.context
    region = context.region
    rv3d = context.region_data
    if not region or not rv3d: return

    axis_length = 0.65 
    drawn_tags = set()

    for obj in _get_drawable_objects(context):
        tags = get_mesh_tags(obj)
        if not tags: continue

        for tag_id, t in tags.items():
            if tag_id in drawn_tags: continue
            drawn_tags.add(tag_id)

            if "center" not in t or t["center"] == (0,0,0): continue
            
            c = mathutils.Vector(t["center"])
            in_front = t.get("in_front", False)

            if not _is_visible(context, region, rv3d, c, in_front):
                continue
                
            pos_center = location_3d_to_region_2d(region, rv3d, c)
            
            if pos_center:
                # Renk tam saf beyaz olarak değiştirildi (R=1, G=1, B=1)
                blf.color(0, 1.0, 1.0, 1.0, 1.0) 
                blf.position(0, pos_center.x + 12, pos_center.y - 12, 0)
                blf.size(0, 14) 
                blf.draw(0, t["name"])

            tip_x = c + mathutils.Vector(t["axis_x"]) * axis_length
            tip_y = c + mathutils.Vector(t["axis_y"]) * axis_length
            tip_z = c + mathutils.Vector(t["axis_z"]) * axis_length
            
            pos_x = location_3d_to_region_2d(region, rv3d, tip_x)
            pos_y = location_3d_to_region_2d(region, rv3d, tip_y)
            pos_z = location_3d_to_region_2d(region, rv3d, tip_z)

            blf.size(0, 12)
            if pos_x:
                blf.color(0, 1.0, 0.2, 0.3, 1.0)
                blf.position(0, pos_x.x, pos_x.y, 0)
                blf.draw(0, "X")
            if pos_y:
                blf.color(0, 0.5, 0.9, 0.2, 1.0)
                blf.position(0, pos_y.x, pos_y.y, 0)
                blf.draw(0, "Y")
            if pos_z:
                blf.color(0, 0.2, 0.5, 1.0, 1.0)
                blf.position(0, pos_z.x, pos_z.y, 0)
                blf.draw(0, "Z")

class ORIENT_OT_create(bpy.types.Operator):
    bl_idname = "orient.create_tag"
    bl_label = "Create Axis"
    
    _last_execution_time = 0.0
    
    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.mode == 'EDIT'

    def execute(self, context):
        current_time = time.time()
        if current_time - ORIENT_OT_create._last_execution_time < 0.3:
            return {'CANCELLED'}
        ORIENT_OT_create._last_execution_time = current_time

        obj = context.active_object
        obj.update_from_editmode()
        
        bm = bmesh.from_edit_mesh(obj.data)
        mw = obj.matrix_world
        
        selected_faces = [f for f in bm.faces if f.select]
        selected_edges = [e for e in bm.edges if e.select]
        
        if selected_faces:
            ref_face = selected_faces[0]
            world_center = mw @ ref_face.calc_center_median()
            
            normal_matrix = mw.to_3x3().inverted().transposed()
            world_normal = (normal_matrix @ ref_face.normal).normalized()
            world_tangent = (mw.to_3x3() @ ref_face.verts[0].co.normalized()).cross(world_normal).normalized()
            world_bitangent = world_normal.cross(world_tangent).normalized()
            geom_type = "FACE"
            
        elif selected_edges:
            ref_edge = selected_edges[0]
            world_center = mw @ ((ref_edge.verts[0].co + ref_edge.verts[1].co) / 2.0)
            
            edge_dir = (mw @ ref_edge.verts[1].co - mw @ ref_edge.verts[0].co).normalized()
            world_tangent = edge_dir
            world_normal = world_tangent.orthogonal()
            world_bitangent = world_normal.cross(world_tangent).normalized()
            geom_type = "EDGE"
        else:
            return {"CANCELLED"}

        tags = get_mesh_tags(obj)
        for t in tags.values():
            dist = (mathutils.Vector(t["center"]) - world_center).length
            if dist < 0.001:
                return {"CANCELLED"}

        rot_matrix = mathutils.Matrix((world_tangent, world_bitangent, world_normal)).transposed()
        existing_names = get_all_custom_orientations()
        
        i = 0
        while f"Axis_{i}" in existing_names:
            i += 1
        name = f"Axis_{i}"
        
        bpy.ops.transform.create_orientation(name=name, use=True, overwrite=True)
        
        custom_orient = context.scene.transform_orientation_slots[0].custom_orientation
        if custom_orient: 
            custom_orient.matrix = rot_matrix
            actual_name = custom_orient.name
        else:
            actual_name = name
            
        tag_key = uuid.uuid4().hex
        
        tags[tag_key] = {
            "name": actual_name, 
            "type": geom_type, 
            "center": tuple(world_center),
            "axis_x": tuple(world_tangent),
            "axis_y": tuple(world_bitangent),
            "axis_z": tuple(world_normal),
            "owner_obj": obj.name,
            "in_front": False
        }
        set_mesh_tags(obj, tags)
        _redraw()
        return {"FINISHED"}

class ORIENT_OT_toggle_in_front(bpy.types.Operator):
    bl_idname = "orient.toggle_in_front"
    bl_label = "Toggle In Front"
    key: bpy.props.StringProperty()

    def execute(self, context):
        obj = context.active_object
        tags = get_mesh_tags(obj)
        if self.key in tags:
            tags[self.key]["in_front"] = not tags[self.key].get("in_front", False)
            set_mesh_tags(obj, tags)
            _redraw()
        return {'FINISHED'}

class ORIENT_OT_rename_tag(bpy.types.Operator):
    bl_idname = "orient.rename_tag"
    bl_label = "Rename Axis"
    bl_property = "new_name"
    key: bpy.props.StringProperty()
    new_name: bpy.props.StringProperty(name="New Name")

    def execute(self, context):
        obj = context.active_object
        tags = get_mesh_tags(obj)
        if self.key not in tags: return {'CANCELLED'}
        
        old_name = tags[self.key]["name"]
        slot = context.scene.transform_orientation_slots[0]
        original_slot_type = slot.type
        actual_new_name = self.new_name

        try:
            slot.type = old_name
            if slot.custom_orientation:
                slot.custom_orientation.name = self.new_name
                actual_new_name = slot.custom_orientation.name
            else:
                actual_new_name = old_name
        except Exception:
            actual_new_name = old_name

        try:
            if original_slot_type == old_name:
                slot.type = actual_new_name
            else:
                slot.type = original_slot_type
        except:
            slot.type = 'GLOBAL'
        
        tags[self.key]["name"] = actual_new_name
        set_mesh_tags(obj, tags)
        _redraw()
        return {'FINISHED'}

    def invoke(self, context, event):
        obj = context.active_object
        tags = get_mesh_tags(obj)
        self.new_name = tags.get(self.key, {}).get("name", "")
        return context.window_manager.invoke_props_dialog(self)

class ORIENT_OT_delete_tag(bpy.types.Operator):
    bl_idname = "orient.delete_tag"
    bl_label = "Delete"
    key: bpy.props.StringProperty()
    
    def execute(self, context):
        obj = context.active_object
        tags = get_mesh_tags(obj)
        if self.key in tags:
            name = tags[self.key]["name"]
            
            slot = context.scene.transform_orientation_slots[0]
            try:
                slot.type = name 
                override = _get_3d_override()
                if override and hasattr(bpy.context, "temp_override"):
                    with bpy.context.temp_override(**override):
                        bpy.ops.transform.delete_orientation()
                else:
                    bpy.ops.transform.delete_orientation()
            except: pass
            
            try: context.scene.transform_orientation_slots[0].type = 'GLOBAL'
            except: pass
            
            del tags[self.key]
            set_mesh_tags(obj, tags)
        _redraw()
        return {'FINISHED'}

class ORIENT_OT_clear_all(bpy.types.Operator):
    bl_idname = "orient.clear_all"
    bl_label = "Clear System"
    def execute(self, context):
        for obj in context.scene.objects:
            if "orient_tags" in obj: del obj["orient_tags"]
        _batch_cache.clear()
        
        override = _get_3d_override()
        custom_types = get_all_custom_orientations()
        slot = bpy.context.scene.transform_orientation_slots[0]
        
        for n in custom_types:
            try:
                slot.type = n
                if override and hasattr(bpy.context, "temp_override"):
                    with bpy.context.temp_override(**override):
                        bpy.ops.transform.delete_orientation()
                else:
                    bpy.ops.transform.delete_orientation()
            except: pass
            
        try: slot.type = 'GLOBAL'
        except: pass

        _redraw()
        return {'FINISHED'}

class ORIENT_PT_panel(bpy.types.Panel):
    bl_label = "Custom Orientation Axis"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Custom Orientations" 

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        
        col = layout.column(align=True)
        col.operator("orient.create_tag", text="Add Axis", icon='ADD')
        col.operator("orient.clear_all", text="Clear Customs", icon='TRASH')
        
        layout.separator()
        
        if obj and obj.type == 'MESH':
            tags = get_mesh_tags(obj)
            my_tags = {k: v for k, v in tags.items() if v.get("owner_obj", "") == obj.name}
            
            if my_tags:
                layout.label(text="Active Axes:")
                box = layout.box()
                for k, t in my_tags.items():
                    row = box.row(align=True)
                    row.label(text=t["name"])
                    
                    icon = 'RADIOBUT_ON' if t.get("in_front", False) else 'RADIOBUT_OFF'
                    front_op = row.operator("orient.toggle_in_front", text="", icon=icon)
                    front_op.key = k

                    rename = row.operator("orient.rename_tag", text="", icon="GREASEPENCIL")
                    rename.key = k
                    dele = row.operator("orient.delete_tag", text="", icon="X")
                    dele.key = k
            else:
                layout.label(text="No axes on this object.", icon='INFO')
        else:
            layout.label(text="Select a mesh to manage.", icon='MESH_DATA')

classes = [ORIENT_OT_create, ORIENT_OT_toggle_in_front, ORIENT_OT_rename_tag, ORIENT_OT_delete_tag, ORIENT_OT_clear_all, ORIENT_PT_panel]

def register():
    for c in classes: bpy.utils.register_class(c)
    
    if "draw_3d" in handle_store:
        try: bpy.types.SpaceView3D.draw_handler_remove(handle_store["draw_3d"], "WINDOW")
        except: pass
    if "draw_2d" in handle_store:
        try: bpy.types.SpaceView3D.draw_handler_remove(handle_store["draw_2d"], "WINDOW")
        except: pass
        
    handle_store["draw_3d"] = bpy.types.SpaceView3D.draw_handler_add(_draw_3d, (), "WINDOW", "POST_VIEW")
    handle_store["draw_2d"] = bpy.types.SpaceView3D.draw_handler_add(_draw_2d, (), "WINDOW", "POST_PIXEL")

def unregister():
    for c in reversed(classes): bpy.utils.unregister_class(c)
    
    if "draw_3d" in handle_store:
        try: bpy.types.SpaceView3D.draw_handler_remove(handle_store["draw_3d"], "WINDOW")
        except: pass
        del handle_store["draw_3d"]
        
    if "draw_2d" in handle_store:
        try: bpy.types.SpaceView3D.draw_handler_remove(handle_store["draw_2d"], "WINDOW")
        except: pass
        del handle_store["draw_2d"]

if __name__ == "__main__":
    register()