import json
import unittest

from backend.delivery_harness import explicit_video_constraints
from backend.coordination import parse_plan


class ExplicitMediaConstraintTests(unittest.TestCase):
    def test_actual_muse_request_does_not_invent_audio_from_coordinated_negation(self):
        prompt = ('Muse实际生成一个约6秒的无声短视频，不需要独立图片，不需要字幕或旁白。'
                  '验收重点是视频文件真实存在、可下载，元数据与抽帧可读；约6秒只是目标，不设严格时长范围。')
        self.assertEqual(explicit_video_constraints(prompt), {'audio_required': False, 'captions_required': False})
        self.assertEqual(explicit_video_constraints('无声、无字幕、无旁白。'),
                         {'audio_required': False, 'captions_required': False})
        self.assertEqual(explicit_video_constraints('No subtitles or voiceover.'),
                         {'audio_required': False, 'captions_required': False})
        self.assertEqual(explicit_video_constraints('Do not require narration. No need for captions.'),
                         {'audio_required': False, 'captions_required': False})

    def test_later_explicit_corrections_override_only_the_addressed_feature(self):
        self.assertEqual(explicit_video_constraints('需要中文旁白和中文字幕。更新：制作无声视频，取消字幕。'),
                         {'audio_required': False, 'captions_required': False})
        self.assertEqual(explicit_video_constraints('制作无声视频，不要字幕。改为添加中文旁白，保留无字幕。'),
                         {'audio_required': True, 'captions_required': False})
        self.assertEqual(explicit_video_constraints('No narration or captions. Update: add English narration and captions.'),
                         {'audio_required': True, 'captions_required': True})
        self.assertEqual(explicit_video_constraints('Add narration and subtitles. Keep it silent.'),
                         {'audio_required': False, 'captions_required': True})

    def test_negation_wraps_positive_verbs_without_inverting_the_requirement(self):
        for prompt in ['不添加旁白', '不需要添加旁白', '请不要再加入中文旁白', '不必额外提供配音',
                       'do not include narration', "don't require voiceover"]:
            self.assertEqual(explicit_video_constraints(prompt), {'audio_required': False}, prompt)
        for prompt in ['不保留字幕', '请不要有字幕', '不需要添加中文字幕', "don't keep captions"]:
            self.assertEqual(explicit_video_constraints(prompt), {'captions_required': False}, prompt)
        self.assertEqual(explicit_video_constraints('不添加旁白和字幕。更新：保留字幕。'),
                         {'audio_required': False, 'captions_required': True})

    def test_mentions_and_titles_do_not_create_an_audio_requirement(self):
        for prompt in ['解释旁白的作用。', '没有提到字幕。', '参考素材名为 Silent Night。', '介绍无声电影的历史。',
                       '不要制作无声视频。', "Don't make a silent video."]:
            self.assertEqual(explicit_video_constraints(prompt), {})
        self.assertEqual(explicit_video_constraints('Make a silent abstract video.'), {'audio_required': False})
        self.assertEqual(explicit_video_constraints('1–2 分钟，添加旁白。'),
                         {'min_duration_seconds': 60, 'max_duration_seconds': 120, 'audio_required': True})

    def test_canonical_plan_overrides_invented_voice_and_caption_checks(self):
        raw = {'title': '无声短片', 'requirements': [{'id': 'film', 'kind': 'video', 'description': '真实短视频',
                'count': 1, 'audio_required': True, 'captions_required': True}],
               'steps': [{'id': 'create', 'member_id': 'muse', 'instruction': '实际制作视频',
                          'expected_output': '视频文件', 'output_kind': 'video', 'output_count': 1, 'depends_on': []}]}
        members = [{'id': 'muse', 'kind': 'muse', 'name': 'Muse', 'model': None}]
        canonical = parse_plan(json.dumps(raw), members, {'images': 0, 'videos': 1, 'provider': 'muse'},
                               'video', '做一个无声短视频，不需要字幕或旁白。')
        video = next(item for item in canonical['requirements'] if item['kind'] == 'video')
        self.assertIs(video['audio_required'], False)
        self.assertIs(video['captions_required'], False)
        self.assertEqual(video['count'], 1)
        self.assertNotIn('min_duration_seconds', video)

