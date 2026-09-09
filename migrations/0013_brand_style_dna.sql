-- S4 创作者风格：brand_profiles 增加 style_dna 列。
-- 六维 Creator DNA（与 douyin-ego-creator 蒸馏口径一致）：
--   contentStrategy / hookDna / narrativeDna / explosionDna /
--   languageDna / conversionDna
-- 存为 JSON 对象（TEXT），可含任意扩展键；NULL = 未蒸馏该画像的风格 DNA。
ALTER TABLE brand_profiles ADD COLUMN style_dna TEXT DEFAULT NULL;
