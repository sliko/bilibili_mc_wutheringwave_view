# bilibili_mc_wutheringwave_view
B站刷播放量 根据关键词搜索视频列表 代理刷取播放量 会导致稿件无法参与活动 例如 鸣潮创作激励计划

单exe，可直接使用
booster_gui.exe

启动命令 
下面的20是进程数,可以修改为你电脑能承受的数值,50是视频想刷的播放数
py fetch_and_boost.py --concurrency 20 --value 50

bv.txt  视频bv号列表
checkerproxy_cache.txt 获取到的所有代理   代理不好用了也可以自己从网上复制进txt
active_proxies_cache.txt 可用代理过滤  根据CPU性能10分钟左右
log.txt 日志

bv号默认自动获取，重新获取需要删掉bv.txt，也支持自己按行填入BV号
获取一次代理后续第二次运行会直接读取，之后运行不删之前的txt的话都是直接读会快很多。
如果第二天使用的话尽量把txt文件删了重新启动，会自动再获取
进程不要开太多  AMD5600 30个左右应该轻松  
播放一次也不要设置太多，设置了3轮循环后就会自动结束，因为代理使用太多次，可能数字不会加
