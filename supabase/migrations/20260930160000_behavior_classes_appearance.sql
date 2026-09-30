insert into app.behavior_classes (key, name, kind, color_token, reliability_text, methodology_version)
values
  ('ear_cover', 'Covering ears', 'self_regulating', '--c-ear', 'From the appearance model. Check when hands leave the frame.', 'xclip-prototypes-v1'),
  ('hair_twirling', 'Hair twirling', 'self_regulating', '--c-hair', 'From the appearance model. Sometimes confuses with face-touching.', 'xclip-prototypes-v1'),
  ('head_nodding', 'Head nodding', 'self_regulating', '--c-nod', 'From the appearance model. Check against natural conversation nods.', 'xclip-prototypes-v1')
on conflict (key) do nothing;
