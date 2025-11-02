"""
配置测试脚本 - 验证 .env 配置是否正确
运行此脚本以检查 MongoDB、OpenAI 和 Gmail 配置
"""

import os
import sys
from dotenv import load_dotenv

# 设置 Windows 控制台编码
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

def test_env_file():
    """测试 .env 文件是否存在"""
    print("\n" + "="*60)
    print("步骤 1: 检查 .env 文件")
    print("="*60)
    
    if not os.path.exists('.env'):
        print("✗ 未找到 .env 文件")
        print("\n请按照以下步骤创建：")
        print("  1. 复制 env_template.txt 的内容")
        print("  2. 创建名为 .env 的文件")
        print("  3. 填入你的配置信息")
        print("\n详细步骤请查看：配置指南_V2.txt")
        return False
    
    print("✓ 找到 .env 文件")
    load_dotenv()
    return True

def test_mongodb():
    """测试 MongoDB 连接"""
    print("\n" + "="*60)
    print("步骤 2: 测试 MongoDB 连接")
    print("="*60)
    
    mongodb_url = os.getenv('MONGODB_URL')
    
    if not mongodb_url:
        print("✗ MONGODB_URL 未设置")
        print("\n请在 .env 文件中添加：")
        print("  MONGODB_URL=your-mongodb-connection-string")
        return False
    
    print(f"MongoDB URL: {mongodb_url[:30]}...")
    
    try:
        from pymongo import MongoClient
        print("正在连接 MongoDB...")
        client = MongoClient(mongodb_url, serverSelectionTimeoutMS=5000)
        # 测试连接
        client.admin.command('ping')
        print("✓ MongoDB 连接成功！")
        
        # 显示数据库信息
        db = client['citadel_scraper']
        collections = db.list_collection_names()
        if collections:
            print(f"  已有集合: {', '.join(collections)}")
        else:
            print("  数据库为空（这是正常的，首次运行会创建）")
        
        client.close()
        return True
        
    except Exception as e:
        print(f"✗ MongoDB 连接失败: {e}")
        print("\n可能的原因：")
        print("  - 连接字符串格式错误")
        print("  - 用户名或密码错误")
        print("  - IP 未加入白名单")
        print("  - 网络连接问题")
        return False

def test_openai():
    """测试 OpenAI API"""
    print("\n" + "="*60)
    print("步骤 3: 测试 OpenAI API")
    print("="*60)
    
    api_key = os.getenv('OPENAI_API_KEY')
    model = os.getenv('MODEL', 'gpt-4o-mini')
    
    if not api_key:
        print("✗ OPENAI_API_KEY 未设置")
        print("\n请在 .env 文件中添加：")
        print("  OPENAI_API_KEY=sk-your-api-key")
        return False
    
    print(f"API Key: {api_key[:20]}...")
    print(f"模型: {model}")
    
    try:
        from openai import OpenAI
        print("正在测试 API 连接...")
        client = OpenAI(api_key=api_key)
        
        # 简单的测试请求
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": "Hello"}
            ],
            max_tokens=10
        )
        
        print("✓ OpenAI API 连接成功！")
        print(f"  响应: {response.choices[0].message.content}")
        print(f"  Token 使用: {response.usage.total_tokens}")
        return True
        
    except Exception as e:
        print(f"✗ OpenAI API 测试失败: {e}")
        print("\n可能的原因：")
        print("  - API Key 无效")
        print("  - 账户余额不足")
        print("  - 模型名称错误")
        print("  - 网络连接问题")
        return False

def test_gmail():
    """测试 Gmail 配置"""
    print("\n" + "="*60)
    print("步骤 4: 测试 Gmail 配置")
    print("="*60)
    
    mail_token = os.getenv('MAIL_TOKEN')
    app_password = os.getenv('APP_PASSWORD')
    recipients = os.getenv('RECIPIENTS', '')
    
    if not mail_token or not app_password:
        print("✗ Gmail 配置不完整")
        print("\n请在 .env 文件中添加：")
        print("  MAIL_TOKEN=your-email@gmail.com")
        print("  APP_PASSWORD=your-app-password")
        return False
    
    print(f"发件人: {mail_token}")
    print(f"应用密码: {'*' * len(app_password)}")
    print(f"收件人: {recipients}")
    
    try:
        import smtplib
        print("正在连接 Gmail SMTP...")
        
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=10) as server:
            server.login(mail_token, app_password)
        
        print("✓ Gmail 认证成功！")
        
        if not recipients:
            print("⚠ 警告: 未设置收件人（RECIPIENTS）")
            print("  邮件将无法发送")
            return False
        
        return True
        
    except Exception as e:
        print(f"✗ Gmail 认证失败: {e}")
        print("\n可能的原因：")
        print("  - 邮箱地址错误")
        print("  - 未启用两步验证")
        print("  - 未使用应用专用密码")
        print("  - 密码输入错误")
        print("\n请访问: https://myaccount.google.com/apppasswords")
        return False

def main():
    print("╔" + "="*58 + "╗")
    print("║" + " "*16 + "配置测试脚本" + " "*28 + "║")
    print("║" + " "*10 + "Citadel Securities 爬虫 V2" + " "*21 + "║")
    print("╚" + "="*58 + "╝")
    
    results = []
    
    # 测试 .env 文件
    if not test_env_file():
        print("\n" + "="*60)
        print("测试中止：请先创建 .env 文件")
        print("="*60)
        return
    
    # 测试各项配置
    results.append(("MongoDB", test_mongodb()))
    results.append(("OpenAI API", test_openai()))
    results.append(("Gmail", test_gmail()))
    
    # 显示总结
    print("\n" + "="*60)
    print("测试总结")
    print("="*60)
    
    for name, result in results:
        status = "✓ 成功" if result else "✗ 失败"
        print(f"{name:15} {status}")
    
    print("="*60)
    
    all_passed = all(result for _, result in results)
    
    if all_passed:
        print("\n🎉 所有配置测试通过！")
        print("\n你现在可以运行爬虫了：")
        print("  - 测试模式: run_scraper_v2_test.bat")
        print("  - 正常模式: run_scraper_v2.bat")
    else:
        print("\n⚠ 部分配置测试失败")
        print("\n请根据上面的错误信息修正配置")
        print("详细配置步骤请查看：配置指南_V2.txt")
    
    print("\n" + "="*60)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n测试已取消")
    except Exception as e:
        print(f"\n✗ 发生错误: {e}")
        import traceback
        traceback.print_exc()

